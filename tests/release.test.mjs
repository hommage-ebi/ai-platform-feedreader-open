const SITE_ID='example-site';
import test from 'node:test';import assert from 'node:assert/strict';
import {transitionRelease} from '../scripts/release-record.mjs';
test('unknown deployment stays verification_pending; successful build alone is not a release',()=>{
 const x=transitionRelease(null,{site_id:SITE_ID,deployment_id:'d1',status:'verification_pending',tests_passed:true,build_passed:true});assert.equal(x.status,'verification_pending');
 assert.throws(()=>transitionRelease(x,{site_id:SITE_ID,deployment_id:'d1',status:'succeeded',tests_passed:true,build_passed:true}),/evidence/);
 assert.throws(()=>transitionRelease(x,{site_id:SITE_ID,deployment_id:'d2',status:'verification_pending'}),/Reconcile/);
 assert.equal(transitionRelease(x,{site_id:SITE_ID,deployment_id:'d1',status:'succeeded',tests_passed:true,build_passed:true,verified_url:'https://reader.example/',verified_at:new Date().toISOString()}).status,'succeeded');
});
