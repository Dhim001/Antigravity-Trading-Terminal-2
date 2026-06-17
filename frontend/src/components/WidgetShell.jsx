import { cn } from '@/lib/utils';
export { IconLabel } from '@/components/ui/icon-label';

/**
 * Shared widget chrome — header, optional toolbar, scrollable body.
 *
 * @param {boolean} [scrollable=false] — apply `.scroll-panel-y` on the body
 * @param {boolean} [scrollPad=true] — include default scroll padding when scrollable
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
  scrollable = false,
  scrollPad = true,
}) {
  return (
    <div className={cn('widget-card flex h-full min-h-0 flex-col overflow-hidden', className)}>
      <div className="widget-header">
        <div className="icon-label-loose min-w-0">
          {Icon && <Icon size={13} className="logo-icon shrink-0" aria-hidden />}
          <span className="widget-title truncate">{title}</span>
        </div>
        {headerRight != null && (
          <div className="flex shrink-0 items-center gap-[var(--icon-gap-loose)]">{headerRight}</div>
        )}
      </div>
      {toolbar}
      <div
        className={cn(
          scrollable
            ? cn('scroll-panel-y', !scrollPad && 'scroll-panel-y-0')
            : 'widget-body',
          contentClassName,
          bodyClassName,
        )}
      >
        {children}
      </div>
    </div>
  );
}

/** Scroll container for dock tables — single scroll owner, no padding */
export function ScrollTablePanel({ children, className, horizontal = false }) {
  if (horizontal) {
    return (
      <div className={cn('algo-bots-scroll-wrap min-h-0 flex-1', className)}>
        <div className="algo-bots-scroll">{children}</div>
      </div>
    );
  }
  return (
    <div className={cn('scroll-panel-y scroll-panel-y-0 min-h-0 flex-1', className)}>
      {children}
    </div>
  );
}

/** Compact toolbar row below widget header (timeframes, filters, etc.) */
export function WidgetToolbar({ children, className, compact = false }) {
  return (
    <div
      className={cn(
        'widget-toolbar',
        compact && 'widget-toolbar--compact',
        className,
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
        'flex h-full min-h-[80px] flex-col items-center justify-center gap-3 p-4 text-muted-foreground',
        className,
      )}
    >
      {Icon && <Icon size={22} className="opacity-50" aria-hidden />}
      <span className="text-xs">{message}</span>
    </div>
  );
}
