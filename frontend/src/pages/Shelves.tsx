import { useState } from 'react';
import { Link, useLocation } from 'wouter';
import { Plus, Globe, Lock } from 'lucide-react';
import { useShelves, useCreateShelf, useMe } from '../lib/queries';
import { Button } from '../components/Button';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { ApiError } from '../lib/api';
import { useT } from '../lib/i18n';
import styles from './Shelves.module.css';

export function Shelves() {
  const t = useT();
  const { data, isLoading, error } = useShelves();
  const me = useMe().data;
  const create = useCreateShelf();
  const [, navigate] = useLocation();

  const [name, setName] = useState('');
  const [isPublic, setIsPublic] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const canMakePublic = !!me?.role?.edit_shelfs;

  const onCreate = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setFormError(null);
    create.mutate(
      { name: trimmed, is_public: isPublic },
      {
        onSuccess: (shelf) => {
          setName('');
          setIsPublic(false);
          navigate(`/shelf/${shelf.id}`);
        },
        onError: (err) =>
          setFormError(err instanceof ApiError ? err.message : t('Could not create shelf.')),
      },
    );
  };

  return (
    <main className={styles.container}>
      <div className={styles.header}>
        <h1 className={styles.title}>{t('Shelves')}</h1>
        {data && <span className={styles.count}>{data.items.length}</span>}
      </div>

      {/* Create */}
      <form className={styles.createForm} onSubmit={onCreate}>
        <input
          className={styles.createInput}
          placeholder={t('New shelf name…')}
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label={t('New shelf name')}
          maxLength={120}
        />
        {canMakePublic && (
          <label className={styles.publicToggle}>
            <input
              type="checkbox"
              checked={isPublic}
              onChange={(e) => setIsPublic(e.target.checked)}
            />
            {t('Public')}
          </label>
        )}
        <Button type="submit" disabled={!name.trim() || create.isPending}>
          <Plus size={16} />
          {t('Create')}
        </Button>
      </form>
      {formError && <p className={styles.formError}>{formError}</p>}

      {isLoading ? (
        <SpinnerCentered size={36} />
      ) : error ? (
        <EmptyState message={error instanceof Error ? error.message : t('Failed to load shelves.')} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState message={t('No shelves yet. Create one above to start collecting books.')} />
      ) : (
        <ul className={styles.grid}>
          {data.items.map((shelf) => (
            <li key={shelf.id}>
              <Link href={`/shelf/${shelf.id}`} className={styles.card}>
                <div className={styles.cardTop}>
                  <span className={styles.shelfName}>{shelf.name}</span>
                  <span
                    className={styles.visibility}
                    title={shelf.is_public ? t('Public shelf') : t('Private shelf')}
                  >
                    {shelf.is_public ? <Globe size={14} /> : <Lock size={14} />}
                  </span>
                </div>
                <div className={styles.cardBottom}>
                  <span className={styles.bookCount}>
                    {shelf.count === 1
                      ? t('{count} book', { count: shelf.count })
                      : t('{count} books', { count: shelf.count })}
                  </span>
                  {!shelf.is_owner && <span className={styles.sharedBadge}>{t('shared')}</span>}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
