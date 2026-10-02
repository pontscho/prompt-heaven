# tests/files/tls — TEST-ONLY TLS material

**TEST-ONLY. LOOPBACK ONLY. NEVER add `test-ca.pem` to any trust store** —
not the system keychain, not a browser, not `SSL_CERT_FILE`, not a CA bundle.
These files exist so the loopback TLS peers in the test fleet (and the Chrome
capture server) can present a certificate that a client verifies against a
pinned CA, instead of switching verification off.

## Files

```
test-ca.pem         the CA CERTIFICATE only (P-256). Its private key is NOT here.
localhost-cert.pem  a leaf certificate signed by that CA (P-256)
localhost-key.pem   the leaf's private key (unencrypted PKCS#8) -- committed on purpose
```

| | CA (`test-ca.pem`) | leaf (`localhost-cert.pem`) |
|---|---|---|
| key | P-256 | P-256 |
| subject | `CN=prompt-heaven TEST-ONLY CA` | `CN=localhost` |
| basicConstraints | `critical, CA:TRUE` | `CA:FALSE` |
| keyUsage | `critical, keyCertSign, cRLSign` | `critical, digitalSignature` |
| extendedKeyUsage | — | `serverAuth` |
| subjectAltName | — | `DNS:localhost, DNS:*.localhost, IP:127.0.0.1` |
| key identifiers | `subjectKeyIdentifier` | `authorityKeyIdentifier` (+ `subjectKeyIdentifier`) |
| validity | 10950 days (~30 years) | 10950 days (~30 years) |

The extension set is chosen so the chain verifies under Python 3.13+'s
`VERIFY_X509_STRICT` (on by default in `ssl.create_default_context()`) as well
as under 3.9's default flags, and under `openssl verify -x509_strict`.

Measured when generated (2026-09-30): `openssl verify -x509_strict` prints OK;
an in-memory `ssl.MemoryBIO` handshake with
`create_default_context(cafile="test-ca.pem")` completes TLS 1.3 for
`server_hostname="localhost"` and `"127.0.0.1"` on Python 3.14.0 (strict flag
set) and 3.9.21, and is refused for `example.com`.

`DNS:*.localhost` is in the SAN because the plan asks for it, but **OpenSSL's
hostname matcher (and therefore Python's) does not honour it**: a wildcard
must be followed by at least two labels, so `a.localhost` is refused with
"Hostname mismatch". Use `localhost` or `127.0.0.1` as the server name.

## Why the CA private key is absent

A committed CA key would let anyone who trusts this CA by mistake be served a
forged certificate for any name, minted by anyone who has a checkout. Without
the key the CA can sign nothing new: the only certificate it will ever vouch
for is the `localhost` leaf below, whose names resolve to loopback. The key was
generated under `.claude/tmp/`, used once to sign the leaf, and deleted.

The leaf key, by contrast, IS committed: the capture server and the loopback
peers must present the leaf, and the leaf is only valid for loopback names.

## Regeneration

Regenerating re-creates BOTH the CA and the leaf (the old CA key is gone, so a
new leaf cannot be signed by the old CA). Run from the repository root, with
OpenSSL 3.x (these were produced with OpenSSL 3.6.0):

```sh
# 1. CA key -- scratch only, never under tests/
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out .claude/tmp/tls-test-ca-key.pem

# 2. CA certificate
openssl req -x509 -new -config /dev/null -key .claude/tmp/tls-test-ca-key.pem -sha256 -days 10950 -subj "/CN=prompt-heaven TEST-ONLY CA" -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" -addext "subjectKeyIdentifier=hash" -out tests/files/tls/test-ca.pem

# 3. leaf key (committed)
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out tests/files/tls/localhost-key.pem

# 4. leaf certificate, signed by the CA
openssl req -x509 -new -config /dev/null -key tests/files/tls/localhost-key.pem -CA tests/files/tls/test-ca.pem -CAkey .claude/tmp/tls-test-ca-key.pem -sha256 -days 10950 -subj "/CN=localhost" -addext "basicConstraints=CA:FALSE" -addext "keyUsage=critical,digitalSignature" -addext "extendedKeyUsage=serverAuth" -addext "subjectAltName=DNS:localhost,DNS:*.localhost,IP:127.0.0.1" -addext "authorityKeyIdentifier=keyid" -out tests/files/tls/localhost-cert.pem

# 5. check, then destroy the CA key
openssl verify -x509_strict -CAfile tests/files/tls/test-ca.pem tests/files/tls/localhost-cert.pem
rm .claude/tmp/tls-test-ca-key.pem
```

`-config /dev/null` keeps the system `openssl.cnf` (whose `[v3_ca]` section
would otherwise inject its own `basicConstraints`) out of the result, so the
extension set is exactly the `-addext` list above plus the key identifiers
OpenSSL adds by default.
