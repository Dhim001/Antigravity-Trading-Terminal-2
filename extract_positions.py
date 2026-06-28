import re

with open('frontend/src/components/ResizableDock.jsx', 'r', encoding='utf-8') as f:
    content = f.read()

start_idx = content.find('const PositionRow = React.memo(')
end_idx = content.find('// ── Orders Tab ──────────────────────────────────────────────────')

positions_code = content[start_idx:end_idx]

imports = """/**
 * PositionsPanel.jsx — Positions dock tab (extracted from ResizableDock).
 */
import React, { useMemo } from 'react';
import { useStore } from '../../store/useStore';
import { sendAction } from '../../api/transport';
import { Action } from '../../api/protocol';
import { priceDecimals, fmtP } from '../../lib/dockFormatters';
import { cn } from '@/lib/utils';
import { Briefcase } from 'lucide-react';
import CollapsibleCard from './CollapsibleCard';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  DataTableRoot,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
  DataTableCell,
} from '../DataTableShell';
import StrategyBadge from '../StrategyBadge';
import { WidgetEmpty } from '../WidgetShell';
import { buildBotLookup, getPositionBots, shortBotId } from '@/lib/botAttribution';
import { selectPositionStats } from '../../store/selectors';
import { useShallow } from 'zustand/react/shallow';

// ── Position Row ──────────────────────────────────────────────────
"""

positions_code = positions_code.replace(
    '<div className="dock-panel-tab">',
    '<div className="dock-panel-tab dock-panel-tab--positions h-full flex flex-col p-2 space-y-2 overflow-y-auto">\n      <CollapsibleCard title="Open Positions" icon={Briefcase} badge={entries.length} className="flex-shrink-0" contentClassName="max-h-[600px] overflow-y-auto">'
)

lines = positions_code.split('\n')
for i in range(len(lines)-1, -1, -1):
    if lines[i] == '    </div>':
        lines[i] = '      </CollapsibleCard>\n    </div>'
        break

positions_code = '\n'.join(lines)

with open('frontend/src/components/dock/PositionsPanel.jsx', 'w', encoding='utf-8') as f:
    f.write(imports + positions_code)
