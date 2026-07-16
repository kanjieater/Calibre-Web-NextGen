import { useState, type MouseEvent as ReactMouseEvent, type ReactNode } from 'react';
import { LifeBuoy, ArrowUpRight, X } from 'lucide-react';
import { useT, type TFunction } from '../lib/i18n';
import styles from './HelpBanner.module.css';

const DISMISS_KEY_PREFIX = 'cwng_banner_dismissed:';
const SUPPORT_PITCH = 'Support us on Ko-fi!';
const SUPPORT_LINK_LABEL = 'Open Ko-fi →';
const SUPPORT_URL = 'https://ko-fi.com/calibrewebnextgen';

type AnnouncementVariant = 'help' | 'support';
type AnnouncementClickAction = 'open-url-and-dismiss';

interface Announcement {
  id: string;
  priority: number;
  content: (t: TFunction) => ReactNode;
  variant: AnnouncementVariant;
  dismissLabel: string;
  legacyDismissKey?: string;
  clickAction?: AnnouncementClickAction;
  url?: string;
}

function KofiMark() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      aria-hidden="true"
      focusable="false"
      className={styles.kofiMark}
    >
      <path className={styles.kofiCup} d="M3.5 6.5h13v6.1a5 5 0 0 1-5 5h-3a5 5 0 0 1-5-5V6.5Z" />
      <path className={styles.kofiHandle} d="M16.5 8h1.75a2.75 2.75 0 0 1 0 5.5H16.2" />
      <path className={styles.kofiHeart} d="M10 14.4 6.9 11.5a1.9 1.9 0 0 1 2.7-2.7l.4.4.4-.4a1.9 1.9 0 1 1 2.7 2.7L10 14.4Z" />
    </svg>
  );
}

/** Add future top-slot announcements here. The queue always selects the highest-
 * priority entry whose durable per-id dismissal has not been recorded. */
const ANNOUNCEMENTS: readonly Announcement[] = [
  {
    id: 'kofi-support-v1',
    priority: 100,
    variant: 'support',
    dismissLabel: 'Dismiss Ko-fi support message',
    legacyDismissKey: 'cwng_kofi_banner_dismissed_v1',
    clickAction: 'open-url-and-dismiss',
    url: SUPPORT_URL,
    content: (t) => (
      <>
        <span className={styles.supportIconWrap}><KofiMark /></span>
        <span className={`${styles.text} ${styles.supportText}`}>
          <span className={styles.pitch}>{t(SUPPORT_PITCH)}</span>
          <span className={styles.supportLink}>{t(SUPPORT_LINK_LABEL)}</span>
        </span>
      </>
    ),
  },
  {
    id: 'help-announcement-v1',
    priority: 200,
    variant: 'help',
    dismissLabel: 'Dismiss help announcement',
    legacyDismissKey: 'cwng_help_banner_dismissed_v1',
    content: (t) => (
      <>
        <span className={styles.iconWrap}>
          <LifeBuoy size={17} aria-hidden="true" focusable={false} />
        </span>
        <span className={styles.text}>
          {t('Need to report an issue? Try the new')} <strong>{t('Help menu')}</strong>
          <ArrowUpRight size={15} className={styles.arrow} aria-hidden="true" focusable={false} />
        </span>
      </>
    ),
  },
];

const PRIORITIZED_ANNOUNCEMENTS = [...ANNOUNCEMENTS].sort(
  (left, right) => right.priority - left.priority,
);

function dismissalKey(id: string) {
  return `${DISMISS_KEY_PREFIX}${id}`;
}

function persistDismissal(id: string) {
  try { localStorage.setItem(dismissalKey(id), '1'); } catch { /* private mode */ }
}

function initialDismissals() {
  const dismissed = new Set<string>();

  for (const announcement of ANNOUNCEMENTS) {
    try {
      const currentKeyDismissed = localStorage.getItem(dismissalKey(announcement.id)) === '1';
      const legacyKeyDismissed = announcement.legacyDismissKey
        ? localStorage.getItem(announcement.legacyDismissKey) === '1'
        : false;

      if (currentKeyDismissed || legacyKeyDismissed) {
        dismissed.add(announcement.id);
        if (!currentKeyDismissed && legacyKeyDismissed) persistDismissal(announcement.id);
      }
    } catch {
      // Storage can be unavailable in private mode; keep the in-memory queue usable.
    }
  }

  return dismissed;
}

export function AnnouncementBanner() {
  const t = useT();
  const [dismissedIds, setDismissedIds] = useState(initialDismissals);
  const announcement = PRIORITIZED_ANNOUNCEMENTS.find(({ id }) => !dismissedIds.has(id));

  if (!announcement) return null;

  const dismiss = () => {
    persistDismissal(announcement.id);
    setDismissedIds((current) => new Set(current).add(announcement.id));
  };

  const activate = (event: ReactMouseEvent<HTMLAnchorElement>) => {
    if (announcement.clickAction !== 'open-url-and-dismiss' || !announcement.url) return;
    event.preventDefault();
    window.open(announcement.url, '_blank', 'noopener,noreferrer');
    dismiss();
  };

  const activateFromAuxClick = (event: ReactMouseEvent<HTMLAnchorElement>) => {
    if (event.button === 1) activate(event);
  };

  const content = announcement.content(t);
  const variantClass = announcement.variant === 'support' ? styles.supportBanner : '';

  return (
    <div
      className={`${styles.banner} ${variantClass}`}
      role="status"
      data-announcement-id={announcement.id}
    >
      {announcement.clickAction === 'open-url-and-dismiss' && announcement.url ? (
        <a
          className={`${styles.bannerSurface} ${styles.clickableSurface}`}
          href={announcement.url}
          target="_blank"
          rel="noopener noreferrer"
          onClick={activate}
          onAuxClick={activateFromAuxClick}
        >
          {content}
        </a>
      ) : (
        <div className={styles.bannerSurface}>{content}</div>
      )}
      <button
        type="button"
        className={styles.close}
        onClick={dismiss}
        aria-label={t(announcement.dismissLabel)}
      >
        <X size={16} aria-hidden="true" focusable={false} />
      </button>
    </div>
  );
}
