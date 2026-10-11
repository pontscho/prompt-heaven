// tf probe: sbx_restrict group L (L10) -- run INSIDE a restricted sbx.
// Prints ONE JSON line: require('os') works (needs the openssl.cnf read,
// sysctl-read and /dev/null rw, M0:5/M0:6), and a write in the cwd.
'use strict';
const tfOs = require('os');
const tfFs = require('fs');
const tfOut = {
  require_os: typeof tfOs.platform() === 'string' ? 'ok' : 'bad',
  cpus: tfOs.cpus().length > 0 ? 'ok' : 'none',
};
try {
  tfFs.writeFileSync('tf_node.txt', 'x');
  tfOut.write = 'ok';
} catch (err) {
  tfOut.write = err.code || String(err);
}
console.log(JSON.stringify(tfOut));
