import {prepareRelease} from './release-snapshot.mjs';
console.log(JSON.stringify(await prepareRelease(),null,2));
