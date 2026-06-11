import { cn } from '@/lib/utils';
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field';
import { ShieldAlert } from 'lucide-react';

/** Grouped admin form block with title + optional description */
export function AdminSection({ title, description, children, className, action, id }) {
  const sectionId = id ?? title.toLowerCase().replace(/\s+/g, '-');
  return (
    <section className={cn('admin-section', className)} aria-labelledby={`admin-section-${sectionId}`}>
      <div className="admin-section-header">
        <div className="min-w-0 flex-1">
          <h3 id={`admin-section-${sectionId}`} className="admin-section-title">{title}</h3>
          {description && <p className="admin-section-desc">{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

/** Label + control + optional hint — matches Order Entry Field pattern */
export function AdminFieldRow({ label, hint, children, className }) {
  return (
    <Field className={cn('gap-2', className)}>
      {label && <FieldLabel className="admin-field-label">{label}</FieldLabel>}
      {children}
      {hint && <FieldDescription className="text-[0.65rem]">{hint}</FieldDescription>}
    </Field>
  );
}

/** Destructive actions — visually separated from everyday admin tasks */
export function AdminDangerZone({ title, description, children, className }) {
  const zoneId = title.toLowerCase().replace(/\s+/g, '-');
  return (
    <section
      className={cn('admin-danger-zone', className)}
      aria-labelledby={`admin-danger-${zoneId}`}
    >
      <div className="admin-danger-zone-header">
        <ShieldAlert className="size-3.5 shrink-0 text-trading-down" aria-hidden />
        <span id={`admin-danger-${zoneId}`} className="admin-danger-zone-title">{title}</span>
      </div>
      {description && <p className="admin-danger-zone-desc">{description}</p>}
      <div className="admin-danger-zone-actions">{children}</div>
    </section>
  );
}

/** Wraps simulation controls when live mode locks them */
export function AdminLockedOverlay({ locked, message, children }) {
  return (
    <div className="relative">
      {locked && (
        <div className="admin-lock-banner" role="status" aria-live="polite">
          <span className="admin-lock-banner-dot" aria-hidden />
          {message}
        </div>
      )}
      <fieldset
        disabled={locked}
        className={cn('admin-locked-fieldset', locked && 'admin-section-locked')}
      >
        {children}
      </fieldset>
    </div>
  );
}
