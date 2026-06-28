import React, { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';

export default function CollapsibleCard({ 
  title, 
  icon: Icon, 
  badge, 
  defaultExpanded = true, 
  className,
  headerClassName,
  contentClassName,
  children,
  action
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  return (
    <div className={cn("rounded-lg border bg-card text-card-foreground shadow-sm overflow-hidden flex flex-col", className)}>
      <div 
        className={cn(
          "flex items-center justify-between px-4 py-2 cursor-pointer hover:bg-muted/50 transition-colors",
          expanded && "border-b",
          headerClassName
        )}
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          {Icon && <Icon size={14} className="text-muted-foreground" />}
          <h3 className="text-sm font-medium">{title}</h3>
          {badge != null && (
            <Badge variant="secondary" className="h-4 min-w-4 px-1 text-[10px] font-bold">
              {badge}
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          {action && (
            <div onClick={(e) => e.stopPropagation()}>
              {action}
            </div>
          )}
          <button 
            className="text-muted-foreground hover:text-foreground opacity-70 hover:opacity-100 transition-opacity"
          >
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        </div>
      </div>
      
      {expanded && (
        <div className={cn("flex-1 min-h-0", contentClassName)}>
          {children}
        </div>
      )}
    </div>
  );
}
