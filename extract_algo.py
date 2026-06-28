import sys

def extract():
    with open('frontend/src/components/ResizableDock.jsx', 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    algo_lines = lines[586:1796] # 0-indexed, 587 is 586
    
    imports = """/**
 * AlgoPanel.jsx — Algo Bot dock tab (extracted from ResizableDock).
 */
import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { toast } from 'sonner';
import { useStore } from '../../store/useStore';
import { sendAction } from '../../api/transport';
import { Action } from '../../api/protocol';
import { fetchBots, withLlmModel } from '../../api/endpoints';
import { getStoreActions } from '../../api/dispatch';
import { selectCashTotal } from '../../store/selectors';
import { useShallow } from 'zustand/react/shallow';
import {
  Cpu, Play, Settings, Trash2, XSquare, ShieldAlert, Pause, PlayCircle, OctagonX,
  RefreshCw, AlertTriangle, Activity, Loader2,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import {
  InputGroup, InputGroupAddon, InputGroupInput, InputGroupText,
} from '@/components/ui/input-group';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Alert, AlertDescription } from '@/components/ui/alert';
import StrategyTemplateCard from '../StrategyTemplateCard';
import StrategyBadge from '../StrategyBadge';
import BacktestResultsPanel from '../BacktestResultsPanel';
import BacktestProgressBar from '../BacktestProgressBar';
import ChartAgentDeployPreview from '../ChartAgentDeployPreview';
import { useVirtualRows } from '../VirtualTableBody';
import { ScrollTablePanel } from '../WidgetShell';
import {
  scheduleBacktestClientTimeout,
  clearBacktestClientTimeout,
  formatBacktestTimeoutLabel,
} from '../../lib/backtestTimeouts';
import { cn } from '@/lib/utils';
import { formatLastSignal } from '@/lib/formatTime';
import { BAR_TIMEFRAMES, deployTimeframeSummary, formatBarTimeframeLabel } from '@/lib/barTimeframes';
import { isLiveMassiveMode, isPaperExecutionMode } from '@/lib/massiveMarket';
import { backtestFingerprint } from '@/lib/backtestDisplay';
import { selectAgentInsight } from '@/lib/agentInsights';
import { isSignalLog, logLineClass } from '@/lib/botLogInsight';

"""
    
    with open('frontend/src/components/dock/AlgoPanel.jsx', 'w', encoding='utf-8') as f:
        f.write(imports)
        f.writelines(algo_lines)

if __name__ == '__main__':
    extract()
