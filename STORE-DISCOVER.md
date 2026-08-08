# Store / Discover (experimental)

Store / Discover is a default-off experimental feature that uses a separately
deployed Shelfmark service as its acquisition engine. It contains no bundled
service credentials or provider keys.

Example deployment configuration (all values intentionally empty):

```env
CWNG_SHELFMARK_URL=
CWNG_SHELFMARK_USERNAME=
CWNG_SHELFMARK_PASSWORD=
CWNG_STORE_SECRET_KEY=
```

`CWNG_STORE_SECRET_KEY`, when supplied, is a canonical URL-safe base64 encoding
of exactly 32 random bytes. When it is empty or absent, CWNG creates
`cwng-store-secret.key` in its writable configuration directory with mode
`0600`. Back up that file with `app.db`; losing it makes stored user credentials
unrecoverable.

An administrator must explicitly enable **Store / Discover** under Experimental
settings and grant users **Store access**. The independent **Store downloads
without approval** role bypasses the request-review queue. Both permissions are
off by default, including for newly created administrators.

Provider credentials are write-only. Users can see only whether their own key
is configured, its last four characters, and its update time. Administrators
can see provider identifiers so they can revoke credentials, but can never read
credential material or last-four metadata.

Shelfmark's active-download endpoint is deployment-wide. CWNG records local
ownership for Store acquisitions, filters progress to the requesting user, and
allows cancel/retry only after an upstream download identifier has been tied to
that local ownership record. Rows that cannot be attributed safely are omitted
rather than exposed across accounts.

Shelfmark's current verified HTTP contract does not define transport for a
caller's per-user provider credential. CWNG stores those credentials safely but
will not invent a header or payload field; an acquisition using a configured
per-user credential returns an explicit unsupported-transport response until
Shelfmark defines that contract.
