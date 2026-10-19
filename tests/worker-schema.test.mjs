import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const migration = readFileSync(new URL('../drizzle/0000_reader_state.sql',import.meta.url),'utf8');
test('migration creates exactly two contract tables with composite keys and defaults',()=>{
  const script = `import sqlite3, json, sys
con=sqlite3.connect(':memory:')
con.executescript(sys.stdin.read())
names=[r[0] for r in con.execute("select name from sqlite_master where type='table' order by name")]
assert names==['article_preferences','reading_state'],names
r=list(con.execute('pragma table_info(reading_state)'))
assert [c[1] for c in r]==['user_id','article_id','read_at']
assert [c[5] for c in r]==[1,2,0]
p=list(con.execute('pragma table_info(article_preferences)'))
assert [c[1] for c in p]==['user_id','article_id','starred','bad','starred_updated_at','bad_updated_at','bad_reason','bad_source','bad_source_message_id']
con.execute("insert into article_preferences(user_id,article_id) values('u','a')")
assert con.execute('select starred,bad from article_preferences').fetchone()==(0,0)
con.execute("insert into reading_state values ('u','a','2026-10-18T00:00:00.000Z')")
con.execute("insert into reading_state values ('other','a','2026-10-18T00:00:00.000Z')")
try:
 con.execute("insert into reading_state values ('u','a','duplicate')")
 raise AssertionError('duplicate accepted')
except sqlite3.IntegrityError: pass
print('SQLite contract verified')`;
  const result=spawnSync(fileURLToPath(new URL('../pipeline/.venv/bin/python',import.meta.url)),['-c',script],{input:migration,encoding:'utf8'});
  assert.equal(result.status,0,result.stderr); assert.match(result.stdout,/verified/);
});
