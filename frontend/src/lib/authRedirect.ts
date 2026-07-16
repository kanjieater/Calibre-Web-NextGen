import { useCallback } from 'react';
import { matchRoute, useLocation, useRouter } from 'wouter';
import type { Parser } from 'wouter';
import { BASE_PREFIX } from './api';
import { AUTH_ROUTES, SPA_ROUTE_PATTERNS } from './routes';

type PostAuthDestination =
  | { kind: 'spa'; path: string }
  | { kind: 'classic'; path: string };

const LIBRARY: PostAuthDestination = { kind: 'spa', path: '/' };

function isSpaRoute(pathname: string, parser: Parser): boolean {
  return SPA_ROUTE_PATTERNS.some((pattern) => matchRoute(parser, pattern, pathname)[0]);
}

/**
 * Resolve the attacker-controlled `next` query parameter into either a wouter
 * path or a same-origin classic-UI path. Invalid, unknown-SPA, and recursive
 * auth destinations deliberately collapse to the library.
 */
export function resolvePostAuthDestination(currentHref: string, parser: Parser): PostAuthDestination {
  const current = new URL(currentHref);
  const next = current.searchParams.get('next');
  if (!next) return LIBRARY;

  // Require one leading slash. Backslashes are rejected before URL parsing
  // because browsers normalize them as path separators in special URLs.
  if (!next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')
      || next.includes('\\')) {
    return LIBRARY;
  }

  let target: URL;
  try {
    target = new URL(next, current.origin);
  } catch {
    return LIBRARY;
  }
  if (target.origin !== current.origin || target.username || target.password) return LIBRARY;

  const pathname = target.pathname;
  if (BASE_PREFIX && pathname !== BASE_PREFIX && !pathname.startsWith(`${BASE_PREFIX}/`)) {
    return LIBRARY;
  }

  const appBase = `${BASE_PREFIX}/app`;
  if (pathname === BASE_PREFIX || pathname === `${BASE_PREFIX}/` || pathname === appBase
      || pathname === `${appBase}/`) {
    return LIBRARY;
  }

  if (pathname.startsWith(`${appBase}/`)) {
    const spaPath = pathname.slice(appBase.length) || '/';
    const normalizedSpaPath = spaPath.replace(/\/+$/, '') || '/';
    if (normalizedSpaPath === AUTH_ROUTES.login || normalizedSpaPath === AUTH_ROUTES.magicLink) return LIBRARY;
    if (!isSpaRoute(spaPath, parser)) return LIBRARY;
    return { kind: 'spa', path: `${spaPath}${target.search}${target.hash}` };
  }

  const classicPath = `${pathname}${target.search}${target.hash}`;
  // Re-parse the exact value handed to location.assign. WHATWG dot-segment
  // normalization can turn a raw path such as `/..//evil.tld` into a
  // protocol-relative `//evil.tld` even though the first origin check passed.
  if (new URL(classicPath, current.origin).origin !== current.origin) return LIBRARY;
  return { kind: 'classic', path: classicPath };
}

export function usePostAuthRedirect() {
  const [, navigate] = useLocation();
  const { parser } = useRouter();
  return useCallback(() => {
    const destination = resolvePostAuthDestination(window.location.href, parser);
    if (destination.kind === 'spa') {
      navigate(destination.path, { replace: true });
    } else {
      window.location.assign(destination.path);
    }
  }, [navigate, parser]);
}
