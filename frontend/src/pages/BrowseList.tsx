import { useMemo, useState } from 'react';
import { Link } from 'wouter';
import { LayoutGrid, List, Search } from 'lucide-react';
import { useEntityList } from '../lib/queries';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import { usePersistentBool } from '../lib/usePersistentBool';
import styles from './BrowseList.module.css';

interface BrowseListProps {
  /** Endpoint/route segment, e.g. "authors". */
  plural: string;
  /** Heading, e.g. "Authors". */
  title: string;
}

export function BrowseList({ plural, title }: BrowseListProps) {
  const t = useT();
  const { data, isLoading, error } = useEntityList(plural);
  const [q, setQ] = useState('');
  const [compact, setCompact] = usePersistentBool('cwng:browse-list-compact', false);

  const items = useMemo(() => {
    const all = data?.items ?? [];
    if (!q.trim()) return all;
    const needle = q.trim().toLowerCase();
    return all.filter((e) => e.name.toLowerCase().includes(needle));
  }, [data, q]);

  return (
    <main className={styles.container}>
      <div className={styles.header}>
        <div className={styles.heading}>
          <h1 className={styles.title}>{t(title)}</h1>
          {data && <span className={styles.count}>{data.items.length}</span>}
        </div>
        <div className={styles.viewToggle} role="group" aria-label={t('View')}>
          <button type="button" onClick={() => setCompact(false)} aria-pressed={!compact} aria-label={t('Grid view')}>
            <LayoutGrid size={17} aria-hidden="true" focusable={false} />
          </button>
          <button type="button" onClick={() => setCompact(true)} aria-pressed={compact} aria-label={t('List view')}>
            <List size={17} aria-hidden="true" focusable={false} />
          </button>
        </div>
      </div>

      {data && data.items.length > 8 && (
        <div className={styles.searchWrap}>
          <Search size={15} className={styles.searchIcon} />
          <input
            type="search"
            className={styles.searchInput}
            placeholder={t('Filter {items}…', { items: t(title).toLocaleLowerCase() })}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label={t('Filter {items}', { items: t(title).toLocaleLowerCase() })}
          />
        </div>
      )}

      {isLoading ? (
        <SpinnerCentered size={36} />
      ) : error ? (
        <EmptyState message={error instanceof Error ? error.message : t('Failed to load.')} />
      ) : items.length === 0 ? (
        <EmptyState message={q
          ? t('No matching {items} for "{query}".', { items: t(title).toLocaleLowerCase(), query: q })
          : t('No {items} yet.', { items: t(title).toLocaleLowerCase() })} />
      ) : (
        <ul className={compact ? styles.list : styles.grid} role="list">
          {items.map((e) => (
            <li key={String(e.id)}>
              <Link href={`/${plural}/${encodeURIComponent(String(e.id))}`} className={styles.item}>
                <span className={styles.name}>{e.name}</span>
                <span className={styles.badge}>{e.count}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
