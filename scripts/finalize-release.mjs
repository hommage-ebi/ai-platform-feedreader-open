import {finalizeRelease} from './release-snapshot.mjs';
console.log(JSON.stringify(await finalizeRelease(),null,2));
