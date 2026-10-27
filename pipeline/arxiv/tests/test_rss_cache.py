"""RSS refresh bypasses distribution caches without refetching saved runs by default."""
import contextlib,io,json,sys,unittest
from unittest.mock import patch
from pipeline.arxiv.tests import test_pipeline
from pipeline.arxiv.collect import collect,fetch
from pipeline.arxiv.workflow import cli,start
from pipeline.shared.runtime import read

URL='https://rss.arxiv.org/rss/cs.LG'
DAY='2026-10-18'

class RSSCacheTests(unittest.TestCase):
    def setUp(self):
        self.f=test_pipeline.PipelineTests();self.f.setUp();self.run=self.f.run;self.root=self.f.root
        self.archive=self.run/f'.cache/feeds/{DAY}/cs.LG.xml'
        self.stamp=self.archive.with_suffix('.metadata.json')
    def tearDown(self):self.f.tearDown()
    def capture(self,body=None,refresh=False):
        calls=[]
        def fetcher(url):calls.append(url);return self.f.rss() if body is None else body
        result=collect(self.run,DAY,feeds=[URL],fetcher=fetcher,refresh_rss=refresh)
        return calls,result
    def test_new_fetch_has_unique_query_and_resume_uses_verified_saved_rss(self):
        with patch('pipeline.arxiv.collect.time.time_ns',side_effect=[100,101]):
            calls,_=self.capture();self.assertEqual(calls,[URL+'?_fetch=100'])
            original=(self.archive.read_bytes(),self.stamp.read_bytes())
            collect(self.run,DAY,feeds=[URL],fetcher=lambda _:self.fail('Resume must not fetch'))
            self.assertEqual((self.archive.read_bytes(),self.stamp.read_bytes()),original)
            attempt=read(self.run/'ledger.json')['stages']['collect_attempt']['details']['scope'][0]
            self.assertTrue(attempt['cached']);self.assertEqual(attempt['url'],URL)
            calls,_=self.capture(refresh=True);self.assertEqual(calls,[URL+'?_fetch=101'])
        metadata=read(self.stamp);self.assertEqual(metadata['url'],URL);self.assertEqual(metadata['request_url'],URL+'?_fetch=101')
        scope=read(self.run/'run/collection-capture.json')['scope']
        self.assertEqual([s['url'] for s in scope],[URL]);self.assertFalse(scope[0]['cached'])
    def test_refresh_adds_new_candidates_and_later_resume_uses_new_feed(self):
        self.capture()
        item='<item><title>New synthetic result</title><link>https://arxiv.org/abs/2610.54321v1</link><description>Synthetic abstract</description></item>'
        body=self.f.rss(item);calls,rows=self.capture(body,refresh=True)
        self.assertEqual(len(calls),1);self.assertEqual(rows[0]['id'],'2610.54321');self.assertEqual(self.archive.read_bytes(),body)
        resumed=collect(self.run,DAY,feeds=[URL],fetcher=lambda _:self.fail('Must use refreshed RSS'))
        self.assertEqual(resumed,rows)
    def test_failed_refresh_preserves_good_archive_and_reports_failed_attempt(self):
        self.capture();original=(self.archive.read_bytes(),self.stamp.read_bytes())
        cases=[self.f.rss(date='Sat, 17 Oct 2026 03:00:00 GMT'),b'not XML',OSError('Network unavailable')]
        for response in cases:
            with self.subTest(response=type(response).__name__):
                def fetcher(_):
                    if isinstance(response,Exception):raise response
                    return response
                collect(self.run,DAY,feeds=[URL],fetcher=fetcher,refresh_rss=True)
                self.assertEqual((self.archive.read_bytes(),self.stamp.read_bytes()),original)
                ledger=read(self.run/'ledger.json')
                self.assertEqual(ledger['stages']['collect_attempt']['status'],'failed')
                self.assertEqual(ledger['stages']['collect']['status'],'complete')
                self.assertTrue(ledger['stages']['collect_attempt']['details']['refresh_rss'])
        collect(self.run,DAY,feeds=[URL],fetcher=lambda _:self.fail('Original cache must remain usable'))
        self.assertEqual(read(self.run/'ledger.json')['stages']['collect_attempt']['status'],'complete')
    def test_stale_initial_response_is_not_saved_and_retry_fetches_again(self):
        self.capture(self.f.rss(date='Sat, 17 Oct 2026 03:00:00 GMT'))
        self.assertFalse(self.archive.exists());self.assertFalse(self.stamp.exists())
        self.assertEqual(read(self.run/'ledger.json')['stages']['collect']['status'],'failed')
        calls,_=self.capture();self.assertEqual(len(calls),1);self.assertTrue(self.archive.exists())
    def test_corrupt_saved_feed_requires_refresh_and_can_be_repaired(self):
        self.capture();self.archive.write_bytes(b'corrupt saved RSS')
        collect(self.run,DAY,feeds=[URL],fetcher=lambda _:self.fail('Do not silently refetch'))
        stage=read(self.run/'ledger.json')['stages']['collect_attempt']
        self.assertEqual(stage['status'],'failed');self.assertIn('--refresh-rss',stage['details']['errors'][0]['error'])
        calls,_=self.capture(refresh=True);self.assertEqual(len(calls),1)
        collect(self.run,DAY,feeds=[URL],fetcher=lambda _:self.fail('Repaired cache must be reusable'))
    def invoke_cli(self,stage,refresh=False,body=None):
        class Response:
            def __init__(inner,url):inner.url=url
            def __enter__(inner):return inner
            def __exit__(inner,*_):return False
            def geturl(inner):return inner.url
            def read(inner):return self.f.rss() if body is None else body
        calls=[]
        def urlopen(request,**kwargs):calls.append(request.full_url);return Response(request.full_url)
        argv=['workflow.py',stage,'--run-dir',str(self.run),'--started-at','2026-10-18T12:00:00+09:00']
        if refresh:argv.append('--refresh-rss')
        with patch.object(sys,'argv',argv),patch('pipeline.arxiv.collect.urllib.request.urlopen',side_effect=urlopen),contextlib.redirect_stdout(io.StringIO()):cli()
        return calls
    def test_collect_cli_passes_refresh_and_uses_cache_by_default(self):
        first=self.invoke_cli('collect');self.assertTrue(first);self.assertTrue(all('?_fetch=' in u for u in first))
        self.assertEqual(self.invoke_cli('collect'),[])
        refreshed=self.invoke_cli('collect',refresh=True);self.assertEqual(len(refreshed),len(first));self.assertTrue(set(refreshed).isdisjoint(first))
    def test_start_cli_passes_refresh_to_today_collection(self):
        first=self.invoke_cli('start');self.assertTrue(first)
        self.assertEqual(self.invoke_cli('start'),[])
        self.assertEqual(len(self.invoke_cli('start',refresh=True)),len(first))
    def test_failed_refresh_is_nonzero_in_collect_cli(self):
        self.invoke_cli('collect')
        with self.assertRaises(SystemExit) as exc:self.invoke_cli('collect',refresh=True,body=b'invalid RSS')
        self.assertEqual(exc.exception.code,1);self.assertEqual(self.invoke_cli('collect'),[])
    def test_invalid_query_and_redirect_cannot_drop_cache_busting_key(self):
        for url in ('https://rss.arxiv.org/rss/cs.LG?other=1','https://rss.arxiv.org.evil/rss/cs.LG','https://rss.arxiv.org/rss/../cs.LG'):
            with patch('pipeline.arxiv.collect.urllib.request.urlopen') as opened,self.assertRaises(ValueError):fetch(url)
            opened.assert_not_called()
        response=unittest.mock.MagicMock();response.__enter__.return_value.geturl.return_value=URL
        with patch('pipeline.arxiv.collect.urllib.request.urlopen',return_value=response),self.assertRaisesRegex(ValueError,'cache key'):fetch(URL+'?_fetch=123')
    def test_refresh_does_not_override_historical_date_guard(self):
        with self.assertRaisesRegex(ValueError,'historical'):collect(self.run,'2026-10-17',feeds=[URL],fetcher=lambda _:self.fail('Must not fetch historical RSS'),refresh_rss=True)

if __name__=='__main__':unittest.main()
