import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Check, Download, KeyRound, RotateCcw, Search, Send, Store as StoreIcon, Trash2, X } from 'lucide-react';
import { BookCover } from '../components/BookCover';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { Spinner, SpinnerCentered } from '../components/Spinner';
import { ApiError, apiPost } from '../lib/api';
import type { StoreCredentialStatus, StoreDownload, StoreRelease, StoreRequest, StoreWork } from '../lib/api';
import { formatAuthors } from '../lib/authors';
import { canAutoApproveStore, canUseStore } from '../lib/permissions';
import {
  useMe, useRevokeStoreCredential, useStoreAcquire,
  useStoreActive, useStoreBootstrap, useStoreCredentials, useStoreDownloadAction,
  useStoreReleases, useStoreRequests, useStoreSearch, useStoreSources,
} from '../lib/queries';
import { useT } from '../lib/i18n';
import styles from './Store.module.css';

const DUPLICATE_MESSAGE = 'Release is already in the download queue';

function authorsOf(work: StoreWork): string[] {
  if (Array.isArray(work.authors)) return work.authors.filter((a): a is string => typeof a === 'string');
  if (typeof work.authors === 'string' && work.authors.trim()) return [work.authors];
  return [];
}

function workKey(work: Pick<StoreWork, 'provider' | 'provider_id'>): string {
  return `${work.provider}:${work.provider_id}`;
}

function releaseKey(release: Pick<StoreRelease, 'source' | 'source_id'>): string {
  return `${release.source}:${release.source_id}`;
}

function itemsOf<T>(value: T[] | { items?: T[]; downloads?: T[]; requests?: T[] } | undefined): T[] {
  if (Array.isArray(value)) return value;
  return value?.items ?? value?.downloads ?? value?.requests ?? [];
}

function formatSize(value: string | number | null | undefined): string {
  if (value == null || value === '') return '';
  if (typeof value === 'string' && /[a-z]/i.test(value)) return value;
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return String(value);
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let n = bytes / 1024;
  let unit = 0;
  while (n >= 1024 && unit < units.length - 1) { n /= 1024; unit += 1; }
  return `${n >= 10 ? n.toFixed(0) : n.toFixed(1)} ${units[unit]}`;
}

function progressOf(download: StoreDownload): number | null {
  const value = typeof download.progress === 'string'
    ? Number.parseFloat(download.progress) : Number(download.progress);
  if (!Number.isFinite(value)) return null;
  return Math.max(0, Math.min(100, value <= 1 ? value * 100 : value));
}

function requestTitle(request: StoreRequest): string {
  return request.release?.title || request.work?.title || request.title || '';
}

export function Store() {
  const t = useT();
  const me = useMe().data;
  const allowed = canUseStore(me);
  const autoApprove = canAutoApproveStore(me);
  const bootstrap = useStoreBootstrap(allowed);
  const sources = useStoreSources(allowed);
  const search = useStoreSearch();
  const acquire = useStoreAcquire();
  const active = useStoreActive(allowed);
  const requests = useStoreRequests(allowed && !autoApprove);
  const downloadAction = useStoreDownloadAction();

  const [query, setQuery] = useState('');
  const [works, setWorks] = useState<StoreWork[]>([]);
  const [selectedWork, setSelectedWork] = useState<StoreWork | null>(null);
  const [selectedRelease, setSelectedRelease] = useState<StoreRelease | null>(null);
  const [selectedSource, setSelectedSource] = useState('direct_download');
  const [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const searchSequence = useRef(0);

  const enabledSources = useMemo(() => (sources.data ?? []).filter((source) => source.enabled), [sources.data]);
  useEffect(() => {
    if (!enabledSources.length) return;
    if (!enabledSources.some((source) => source.name === selectedSource)) {
      setSelectedSource(enabledSources[0].name);
    }
  }, [enabledSources, selectedSource]);

  const sourceAvailable = enabledSources.some((source) => source.name === selectedSource);
  const releases = useStoreReleases(sourceAvailable ? selectedWork : null, selectedSource);

  // A release belongs to exactly one selected work/source. Clear it before a new
  // release query can paint, so a stale edition can never be submitted.
  useEffect(() => { setSelectedRelease(null); }, [selectedWork, selectedSource]);

  if (!allowed) return null;

  const runSearch = (event: React.FormEvent) => {
    event.preventDefault();
    const term = query.trim();
    if (!term) return;
    setSelectedWork(null);
    setSelectedRelease(null);
    setWorks([]);
    setMessage(null);
    const mine = ++searchSequence.current;
    search.mutate(term, {
      onSuccess: (data) => { if (mine === searchSequence.current) setWorks(data.books ?? []); },
      onError: (error) => {
        if (mine !== searchSequence.current) return;
        setMessage({
          kind: 'error',
          text: error instanceof ApiError ? error.message : t('Could not search the Store.'),
        });
      },
    });
  };

  const submitSelection = () => {
    if (!selectedWork || !selectedRelease || !validRelease(selectedRelease, selectedSource)) return;
    setMessage(null);
    acquire.mutate({
      work: { provider: selectedWork.provider, provider_id: selectedWork.provider_id },
      release: {
        provider: selectedWork.provider,
        book_id: selectedWork.provider_id,
        source: selectedRelease.source || selectedSource,
        source_id: selectedRelease.source_id,
        title: selectedRelease.title,
        format: selectedRelease.format!,
        size: selectedRelease.size ?? null,
        extra: selectedRelease.extra ?? {},
      },
    }, {
      onSuccess: (result) => {
        const alreadyQueued = result.already_queued === true || result.status === 'already_queued';
        setMessage({
          kind: 'ok',
          text: alreadyQueued
            ? t('Already queued.')
            : result.mode === 'request'
              ? t('Request sent for approval.')
              : t('Download queued.'),
        });
      },
      onError: (error) => {
        // Shelfmark's duplicate detection is correct but its status code is not.
        // Treat only this exact upstream pair as the benign idempotent outcome.
        if (error instanceof ApiError && error.status === 500 && error.message === DUPLICATE_MESSAGE) {
          setMessage({ kind: 'ok', text: t('Already queued.') });
          return;
        }
        setMessage({
          kind: 'error',
          text: error instanceof ApiError ? error.message : t('Could not queue this release.'),
        });
      },
    });
  };

  const releaseItems = releases.data?.releases ?? [];
  const activeItems = itemsOf(active.data);
  const requestItems = itemsOf(requests.data);
  const unavailable = bootstrap.error || sources.error;

  return (
    <div className={styles.container} data-testid="store-page">
      <header className={styles.header}>
        <span className={styles.headerIcon} aria-hidden="true"><StoreIcon size={24} focusable={false} /></span>
        <div>
          <h1 className={styles.title}>{t('Store')}</h1>
          <p className={styles.subtitle}>{t('Find a work, then choose the exact edition you want.')}</p>
        </div>
      </header>

      <form className={styles.search} role="search" onSubmit={runSearch}>
        <label className={styles.searchField}>
          <span className="sr-only">{t('Search the Store')}</span>
          <Search size={17} aria-hidden="true" focusable={false} />
          <input value={query} type="search" onChange={(event) => setQuery(event.target.value)}
            placeholder={t('Title, author, or ISBN')} />
        </label>
        <Button type="submit" disabled={!query.trim() || search.isPending}>
          {search.isPending ? <Spinner size={16} /> : <Search size={16} aria-hidden="true" focusable={false} />}
          {t('Search')}
        </Button>
      </form>

      <span className={message?.kind === 'error' ? styles.error : styles.status}
        data-testid="store-acquire-status"
        role={message?.kind === 'error' ? 'alert' : 'status'}>{message?.text}</span>

      {unavailable ? (
        <EmptyState message={t('The Store is not available right now.')} />
      ) : search.isPending ? (
        <SpinnerCentered size={36} />
      ) : works.length > 0 ? (
        <section className={styles.section} aria-labelledby="store-work-heading">
          <div className={styles.sectionHead}>
            <h2 id="store-work-heading">{t('Choose the work')}</h2>
            <span role="status">{t('{count} matches', { count: works.length })}</span>
          </div>
          <div className={styles.workGrid} role="list">
            {works.map((work) => {
              const key = workKey(work);
              const chosen = selectedWork ? workKey(selectedWork) === key : false;
              const authors = authorsOf(work);
              return (
                <div key={key} role="listitem">
                  <button type="button" className={chosen ? styles.workCardSelected : styles.workCard}
                    aria-pressed={chosen}
                    aria-label={chosen ? t('Selected {title}', { title: work.title }) : t('Choose {title}', { title: work.title })}
                    onClick={() => { setSelectedWork(work); setMessage(null); }}>
                    <BookCover coverUrl={work.cover_url} title={work.title} authors={authors} />
                    <span className={styles.workInfo}>
                      <strong>{work.title}</strong>
                      <span>{formatAuthors(authors) || t('Unknown author')}</span>
                      <small>{[work.publish_year, work.publisher, work.isbn_13].filter(Boolean).join(' · ')}</small>
                      <small>{work.provider}</small>
                    </span>
                    {chosen && <span className={styles.selectedBadge}><Check size={14} aria-hidden="true" focusable={false} /> {t('Selected')}</span>}
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      ) : search.isSuccess ? (
        <EmptyState message={t('No matching works found.')} />
      ) : null}

      {selectedWork && (
        <section className={styles.section} aria-labelledby="store-release-heading">
          <div className={styles.sectionHead}>
            <div>
              <h2 id="store-release-heading">{t('Choose an edition')}</h2>
              <p>{t('Release details come from the acquisition source so you can verify format, size, and language.')}</p>
            </div>
          </div>

          {sources.isLoading ? (
            <SpinnerCentered size={28} />
          ) : sources.error ? (
            <EmptyState message={t('Could not load release sources.')} />
          ) : enabledSources.length === 0 ? (
            <EmptyState message={t('No release sources are enabled.')} />
          ) : (
            <div className={styles.sources} role="group" aria-label={t('Release source')}>
              {enabledSources.map((source) => (
                <button key={source.name} type="button" aria-pressed={selectedSource === source.name}
                  onClick={() => setSelectedSource(source.name)}>
                  {source.display_name || source.name}
                </button>
              ))}
            </div>
          )}

          {enabledSources.length === 0 ? null : releases.isLoading ? (
            <SpinnerCentered size={32} />
          ) : releases.error ? (
            <EmptyState message={releases.error instanceof Error ? releases.error.message : t('Could not load editions.')} />
          ) : releaseItems.length === 0 ? (
            <EmptyState message={t('No editions found from this source.')} />
          ) : (
            <ul className={styles.releaseList} role="list">
              {releaseItems.map((release) => {
                const key = releaseKey(release);
                const chosen = selectedRelease ? releaseKey(selectedRelease) === key : false;
                const metadata = [release.format, formatSize(release.size), release.language,
                  release.publish_year, release.publisher].filter(Boolean);
                return (
                  <li key={key}>
                    <button type="button" className={chosen ? styles.releaseSelected : styles.release}
                      aria-pressed={chosen}
                      onClick={() => { setSelectedRelease(release); setMessage(null); }}>
                      {release.cover_url && (
                        <span className={styles.releaseCover}>
                          <BookCover coverUrl={release.cover_url} title={release.title} />
                        </span>
                      )}
                      <span className={styles.releaseInfo}>
                        <strong>{release.title}</strong>
                        <span>{metadata.join(' · ') || t('No additional edition details')}</span>
                        <small>{release.source || selectedSource}</small>
                      </span>
                      <span className={styles.releaseChoice}>
                        {chosen ? <><Check size={15} aria-hidden="true" focusable={false} /> {t('Selected')}</> : t('Select')}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          <div className={styles.acquireBar}>
            <div>
              <strong>{autoApprove || bootstrap.data?.auto_approve ? t('Download this edition') : t('Request this edition')}</strong>
              <span>{selectedRelease ? selectedRelease.title : t('Choose an edition to continue.')}</span>
            </div>
            <Button type="button"
              disabled={!selectedRelease || !validRelease(selectedRelease, selectedSource) || acquire.isPending}
              onClick={submitSelection}>
              {autoApprove || bootstrap.data?.auto_approve
                ? <Download size={16} aria-hidden="true" focusable={false} />
                : <Send size={16} aria-hidden="true" focusable={false} />}
              {acquire.isPending ? t('Sending…') : autoApprove || bootstrap.data?.auto_approve ? t('Download') : t('Request')}
            </Button>
          </div>
        </section>
      )}

      <DownloadActivity items={activeItems} pending={downloadAction.isPending}
        onAction={(bookId, action) => downloadAction.mutate({ bookId, action })} />
      {!autoApprove && <MyRequests items={requestItems} loading={requests.isLoading} error={requests.error} />}
      <StoreCredentials providers={bootstrap.data?.credential_providers ?? []} />
    </div>
  );
}

function validRelease(release: StoreRelease, fallbackSource: string): boolean {
  return Boolean(
    String(release.title || '').trim()
    && String(release.source || fallbackSource).trim()
    && String(release.source_id || '').trim()
    && String(release.format || '').trim(),
  );
}

function DownloadActivity({ items, pending, onAction }: {
  items: StoreDownload[];
  pending: boolean;
  onAction: (bookId: string | number, action: 'cancel' | 'retry') => void;
}) {
  const t = useT();
  if (!items.length) return null;
  return (
    <section className={styles.section} aria-labelledby="store-download-heading">
      <h2 id="store-download-heading">{t('Active downloads')}</h2>
      <ul className={styles.activityList} role="list">
        {items.map((item, index) => {
          const actionBookId = item.book_id;
          const displayId = item.book_id ?? item.id;
          const progress = progressOf(item);
          const canRetry = String(item.status || '').toLowerCase().includes('fail');
          return (
            <li key={`${String(displayId ?? item.title ?? item.status ?? 'download')}:${index}`}>
              <div className={styles.activityInfo}>
                <strong>{item.title || t('Store download')}</strong>
                <span>{[item.format, item.source, item.status].filter(Boolean).join(' · ')}</span>
                {progress != null && (
                  <div className={styles.progress} role="progressbar" aria-label={t('Download progress')}
                    aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)}>
                    <span style={{ width: `${progress}%` }} />
                  </div>
                )}
              </div>
              {actionBookId != null && String(actionBookId).trim() && (
                <button type="button" className={styles.iconButton} disabled={pending}
                  aria-label={canRetry ? t('Retry {title}', { title: item.title || t('download') }) : t('Cancel {title}', { title: item.title || t('download') })}
                  onClick={() => onAction(actionBookId, canRetry ? 'retry' : 'cancel')}>
                  {canRetry
                    ? <RotateCcw size={16} aria-hidden="true" focusable={false} />
                    : <X size={16} aria-hidden="true" focusable={false} />}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function MyRequests({ items, loading, error }: { items: StoreRequest[]; loading: boolean; error: unknown }) {
  const t = useT();
  return (
    <section className={styles.section} aria-labelledby="store-request-heading">
      <h2 id="store-request-heading">{t('My requests')}</h2>
      {loading ? <SpinnerCentered size={28} /> : error ? (
        <EmptyState message={error instanceof Error ? error.message : t('Could not load your requests.')} />
      ) : items.length === 0 ? (
        <EmptyState message={t('You have no pending requests.')} />
      ) : <ul className={styles.requestList} role="list">
        {items.map((request) => (
          <li key={String(request.id)}>
            <strong>{requestTitle(request) || t('Untitled request')}</strong>
            <span>{request.status || t('Pending')}</span>
          </li>
        ))}
      </ul>}
    </section>
  );
}

function StoreCredentials({ providers }: { providers: { key: string; label: string }[] }) {
  const t = useT();
  const queryClient = useQueryClient();
  const credentials = useStoreCredentials(true);
  const revoke = useRevokeStoreCredential();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [savingProvider, setSavingProvider] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const statuses = new Map((credentials.data?.items ?? []).map((status) => [status.provider, status]));

  const submit = (event: React.FormEvent, provider: string) => {
    event.preventDefault();
    const credential = drafts[provider]?.trim();
    if (!credential) return;
    setMessage(null);
    setSavingProvider(provider);
    void apiPost<StoreCredentialStatus>(
      `/api/v1/store/credentials/${encodeURIComponent(provider)}`, { credential },
    ).then(() => {
        // Plaintext exists only in this controlled input and the immediate POST.
        // Clear it as soon as the write-only boundary accepts it.
        setDrafts((current) => ({ ...current, [provider]: '' }));
        setMessage({ ok: true, text: t('Credential saved.') });
        void queryClient.invalidateQueries({ queryKey: ['store', 'credentials'] });
      }).catch((error: unknown) => setMessage({
        ok: false,
        text: error instanceof ApiError ? error.message : t('Could not save credential.'),
      })).finally(() => setSavingProvider(null));
  };

  return (
    <section className={styles.section} aria-labelledby="store-credential-heading">
      <div className={styles.sectionHead}>
        <div>
          <h2 id="store-credential-heading"><KeyRound size={17} aria-hidden="true" focusable={false} /> {t('Provider credentials')}</h2>
          <p>{t('Your paid provider keys are encrypted in the server database. They are write-only here: neither you nor an administrator can reveal a saved key. Administrators can only revoke it.')}</p>
        </div>
      </div>
      <span className={message ? (message.ok ? styles.status : styles.error) : undefined}
        role={message?.ok === false ? 'alert' : 'status'}>{message?.text}</span>
      {credentials.isLoading ? <SpinnerCentered size={28} /> : credentials.error ? (
        <EmptyState message={credentials.error instanceof Error ? credentials.error.message : t('Could not load credentials.')} />
      ) : (
        <div className={styles.credentialList}>
          {providers.map((provider) => {
            const status = statuses.get(provider.key);
            const inputId = `store-credential-${provider.key}`;
            return (
              <form key={provider.key} className={styles.credentialRow}
                onSubmit={(event) => submit(event, provider.key)}>
                <div className={styles.credentialLabel}>
                  <label htmlFor={inputId}>{t(provider.label)}</label>
                  {status && <span>{t('Configured · ending in {last4}', { last4: status.last4 || '••••' })}</span>}
                </div>
                <input id={inputId} type="password" autoComplete="new-password"
                  value={drafts[provider.key] ?? ''}
                  placeholder={status ? t('Enter a replacement key') : t('Enter provider key')}
                  onChange={(event) => setDrafts((current) => ({ ...current, [provider.key]: event.target.value }))} />
                <Button type="submit" size="sm" disabled={!drafts[provider.key]?.trim() || savingProvider !== null}>
                  {status ? t('Replace') : t('Save')}
                </Button>
                {status && (
                  <button type="button" className={styles.iconButton}
                    disabled={revoke.isPending}
                    aria-label={t('Revoke {provider} credential', { provider: t(provider.label) })}
                    onClick={() => {
                      if (!window.confirm(t('Revoke the saved {provider} credential?', { provider: t(provider.label) }))) return;
                      revoke.mutate(provider.key, {
                        onSuccess: () => setMessage({ ok: true, text: t('Credential revoked.') }),
                        onError: () => setMessage({ ok: false, text: t('Could not revoke credential.') }),
                      });
                    }}>
                    <Trash2 size={16} aria-hidden="true" focusable={false} />
                  </button>
                )}
              </form>
            );
          })}
        </div>
      )}
    </section>
  );
}
