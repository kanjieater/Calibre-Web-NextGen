import { useState } from 'react';
import { LifeBuoy, ArrowUpRight, X } from 'lucide-react';
import { useT } from '../lib/i18n';
import styles from './HelpBanner.module.css';

const HELP_DISMISS_KEY = 'cwng_help_banner_dismissed_v1';
const KOFI_DISMISS_KEY = 'cwng_kofi_banner_dismissed_v1';
const SUPPORT_PITCH = 'Less than Netflix to keep us afloat';
const SUPPORT_LINK_LABEL = 'Join on Ko-fi →';
const SUPPORT_URL = 'https://ko-fi.com/calibrewebnextgen';

function wasDismissed(key: string) {
  try { return localStorage.getItem(key) === '1'; } catch { return false; }
}

function persistDismissal(key: string) {
  try { localStorage.setItem(key, '1'); } catch { /* private mode */ }
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

/** A one-time, dismissible nudge shown in the new UI pointing users at the new
 *  Help menu (top-right) for reporting issues. Deliberately a cool teal tone —
 *  distinct from the app's warm amber accent and from the amber update banner —
 *  so it reads as a separate, friendly heads-up. Dismissal persists. */
export function HelpBanner() {
  const t = useT();
  const [helpDismissed, setHelpDismissed] = useState(() => wasDismissed(HELP_DISMISS_KEY));
  const [kofiDismissed, setKofiDismissed] = useState(() => wasDismissed(KOFI_DISMISS_KEY));

  const dismissHelp = () => {
    persistDismissal(HELP_DISMISS_KEY);
    setHelpDismissed(true);
  };

  const dismissKofi = () => {
    persistDismissal(KOFI_DISMISS_KEY);
    setKofiDismissed(true);
  };

  if (helpDismissed && kofiDismissed) return null;

  if (helpDismissed) {
    return (
      <div className={`${styles.banner} ${styles.supportBanner}`} role="status">
        <span className={styles.supportIconWrap}><KofiMark /></span>
        <span className={`${styles.text} ${styles.supportText}`}>
          <span className={styles.pitch}>{t(SUPPORT_PITCH)}</span>
          <a
            className={styles.supportLink}
            href={SUPPORT_URL}
            target="_blank"
            rel="noopener noreferrer"
          >
            {t(SUPPORT_LINK_LABEL)}
          </a>
        </span>
        <button
          type="button"
          className={styles.close}
          onClick={dismissKofi}
          aria-label={t('Dismiss Ko-fi support message')}
        >
          <X size={16} aria-hidden="true" focusable={false} />
        </button>
      </div>
    );
  }

  return (
    <div className={styles.banner} role="status">
      <span className={styles.iconWrap}>
        <LifeBuoy size={17} aria-hidden="true" focusable={false} />
      </span>
      <span className={styles.text}>
        {t('Need to report an issue? Try the new')} <strong>{t('Help menu')}</strong>
        <ArrowUpRight size={15} className={styles.arrow} aria-hidden="true" focusable={false} />
      </span>
      <button
        type="button"
        className={styles.close}
        onClick={dismissHelp}
        aria-label={t('Dismiss help announcement')}
      >
        <X size={16} aria-hidden="true" focusable={false} />
      </button>
    </div>
  );
}
