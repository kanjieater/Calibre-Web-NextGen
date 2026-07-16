# SPA login `next` redirect — security review

Reviewed rebased branch HEAD on 2026-07-15. This review covers the attacker-controlled
`next` value consumed after SPA password and magic-link authentication.

## Gate result

**Clean for the reviewed open-redirect, script-scheme, mount-escape, and recursive-auth
boundaries.** No production changes were required by this review.

## Input-to-sink trace

1. **OBSERVED:** `resolvePostAuthDestination()` reads `next` through
   `new URL(currentHref).searchParams.get('next')`; percent-encoding is therefore decoded
   before validation.
2. **OBSERVED:** a destination must start with exactly one `/`. Protocol-relative values,
   all backslashes, and ASCII scheme-bearing values are rejected before parsing.
3. **OBSERVED:** the remaining value is parsed with `new URL(next, current.origin)` and
   its origin is compared to `current.origin`; parsed credentials are also rejected.
4. **OBSERVED:** `target.pathname` is URL-normalized before the reverse-proxy mount check.
   With a non-empty `BASE_PREFIX`, only the prefix itself or descendants survive, so dot
   segments cannot escape the mount.
5. **OBSERVED:** paths under `<prefix>/app/` are classified as SPA destinations only when
   wouter matches a pattern from `SPA_ROUTE_PATTERNS`. Those patterns are the values of
   `SPA_ROUTES`, and `App.tsx` renders the same constants. Unknown SPA paths and `/login`
   or `/magic-link` recursion fall back to the library.
6. **OBSERVED:** accepted SPA destinations reach wouter `navigate(..., { replace: true })`;
   accepted classic destinations reach `window.location.assign()`. Neither path writes
   attacker content to an HTML/JavaScript sink.

## Adversarial evidence

**OBSERVED:** `npm run test:e2e -- login-redirect.spec.ts` on the rebuilt final image:
`18 passed, 3 skipped`. Both desktop and mobile reject `https://evil.tld`, `//evil.tld`,
`/\\evil.tld`, `javascript:alert(1)`, and `data:text/html,phish`; the desktop password and
real two-context magic-link flows complete at the library.

**OBSERVED:** the reverse-proxy test uses a real nginx `/cwa/` mount with
`X-Script-Name: /cwa`. A prefixed destination remains inside `/cwa`; same-origin
`/admin/config` outside that mount is rejected. Result: `5 passed`.

**OBSERVED:** a separate live Chromium matrix at desktop 1280x800 and mobile 390x844,
under both dark and light themes, exercised no `next`, `/`, a real deep SPA book path,
classic `/admin/config`, `//evil.tld`, `/\\evil.tld`, and `javascript:`. All four cells
passed with zero browser-console errors.

## Residual boundary

**OBSERVED:** a same-origin classic path within the configured mount is intentionally
allowed. Authorization remains the responsibility of the destination endpoint; this
redirect does not bypass its authentication or CSRF checks.

**ASSUMED:** deployment proxies preserve the configured mount semantics. The application
now sanitizes the prefix it stamps into the SPA shell, and the live nginx test covers the
normal `/cwa` contract, but arbitrary third-party proxy rewrites were not exhaustively
deployed and tested here.

## Deliberately not claimed

- No new backend route, session primitive, credential handling, or authorization rule is
  introduced by this branch.
- This review does not certify unrelated same-origin classic endpoints; it certifies that
  `next` cannot directly select another origin, a script/data scheme, an unknown SPA route,
  or a path outside the reverse-proxy mount.
