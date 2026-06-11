import { cn } from '@/lib/utils';

/**
 * Consistent icon + label row. Use in headers, tabs, list rows.
 * Icons inside Button/TabsTrigger should use data-icon="inline-start" instead.
 */
export function IconLabel({
  icon: Icon,
  children,
  size = 13,
  variant = 'default',
  className,
  iconClassName,
  ...props
}) {
  const gapClass =
    variant === 'tight' ? 'icon-label-tight'
    : variant === 'loose' ? 'icon-label-loose'
    : 'icon-label';

  return (
    <span className={cn(gapClass, className)} {...props}>
      {Icon && (
        <Icon
          size={size}
          className={cn('shrink-0', iconClassName)}
          aria-hidden
        />
      )}
      {children}
    </span>
  );
}
