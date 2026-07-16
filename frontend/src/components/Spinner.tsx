import styles from './Spinner.module.css';
import { useT } from '../lib/i18n';

interface SpinnerProps {
  size?: number;
}

export function Spinner({ size = 28 }: SpinnerProps) {
  const t = useT();
  return (
    <span
      className={styles.ring}
      style={{ width: size, height: size, borderWidth: Math.max(2, size / 10) }}
      role="status"
      aria-label={t('Loading')}
    />
  );
}

export function SpinnerCentered({ size = 36 }: SpinnerProps) {
  return (
    <div className={styles.centered}>
      <Spinner size={size} />
    </div>
  );
}
