import test from 'node:test';
import assert from 'node:assert/strict';
import {pythonStatusFetcher} from '../scripts/sync-release-status.mjs';

test('Python status transport rejects non-HTTPS destinations without exposing credentials',async()=>{
 const secret='private-fixture-value';
 await assert.rejects(pythonStatusFetcher()('http://reader.example/api/deployment-status',{headers:{'OAI-Sites-Authorization':secret}}),error=>{
  assert.match(error.message,/Site status transport failed/);
  assert.ok(!error.message.includes(secret));return true;
 });
});
