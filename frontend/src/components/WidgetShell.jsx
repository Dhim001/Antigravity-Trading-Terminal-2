import { cn } from '@/lib/utils';

/**
 * Shared widget chrome — header, optional toolbar, scrollable body.
 */
export function WidgetShell({
  icon: Icon,
  title,
  headerRight,
  toolbar,
  children,
  className,
  contentClassName,
  bodyClassName,
}) {
  return (
    <div className={cn('widget-card flex h-full min-h-0 flex-col overflow-hidden', className)}>
      <div className="widget-header">
        <div className="flex min-w-0 items-center gap-1.5">
          {Icon && <Icon size={13} className="logo-icon shrink-0" />}
          <span className="widget-title truncate">{title}</span>
        </div>
        {headerRight != null && (
          <div className="flex shrink-0 items-center gap-1.5">{headerRight}</div>
        )}
      </div>
      {toolbar}
      <div className={cn('widget-content min-h-0 flex-1', contentClassName, bodyClassName)}>
        {children}
      </div>
    </div>
  );
}

/** Compact toolbar row below widget header (timeframes, filters, etc.) */
export function WidgetToolbar({ children, className }) {
  return (
    <div
      className={cn(
        'flex shrink-0 flex-wrap items-center gap-1 border-b border-border bg-muted/25 px-2.5 py-1',
        className
      )}
    >
      {children}
    </div>
  );
}

export function WidgetToolbarDivider() {
  return <div className="mx-0.5 h-3.5 w-px shrink-0 bg-border" aria-hidden />;
}

/** Centered empty placeholder inside widget body */
export function WidgetEmpty({ icon: Icon, message, className }) {
  return (
    <div
      className={cn(
        'flex h-full min-h-[80px] flex-col items-center justify-center gap-2 p-4 text-muted-foreground',
        className
      )}
    >
      {Icon && <Icon size={22} className="opacity-30" />}
      <span className="text-xs">{message}</span>
    </div>
  );
}
