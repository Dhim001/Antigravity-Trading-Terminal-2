/**
 * PortfolioDashboard — full-window portfolio analytics workspace.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty';
import {
  AlertCircle, BarChart2, LayoutDashboard, Loader2, RefreshCw, ShieldAlert, TrendingUp,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useAnalytics, fetchBenchmarks } from '@/hooks/useAnalytics';
import { invokeHttpAction, sendAction } from '@/api/transport';
import { Action } from '@/api/protocol';
import { useStore } from '@/store/useStore';
import { useSettingsStore } from '@/store/useSettingsStore';
import { StatCard } from './StatCard';
import PnlCalendar from './analytics/PnlCalendar';
import CorrelationMatrix from './analytics/CorrelationMatrix';
import ExposureHeatmap from './analytics/ExposureHeatmap';
import StatsBreakdownTable from './analytics/StatsBreakdownTable';
import TradeJournal from './analytics/TradeJournal';
import { ANALYTICS_PERIODS, buildPortfolioInvalidateKey, fmtPct, fmtUsd, pnlTone } from '@/lib/analytics/helpers';

function EChartPanel({ option, className, deps = [] }) {
  const ref = useRef(null);
  const inst = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    let chart = null;
    let disposed = false;
    const mount = () => {
      if (disposed || chart) return;
      if (el.clientWidth < 2 || el.clientHeight < 2) return;
      chart = echarts.init(el, 'dark');
      inst.current = chart;
    };
    const ro = new ResizeObserver(() => {
      if (chart) chart.resize();
      else mount();
    });
    ro.observe(el);
    mount();
    return () => {
      disposed = true;
      ro.disconnect();
      chart?.dispose();
      inst.current = null;
    };
  }, []);

  useEffect(() => {
    if (!inst.current || !option) return;
    inst.current.setOption(option, { notMerge: true });
  }, [option, ...deps]);

  return <div ref={ref} className={cn('min-h-[180px] w-full min-w-0', className)} />;
}

function ChartCard({
  title,
  description,
  children,
  className,
  contentClassName,
  headerActions,
}) {
  return (
    <Card className={cn('portfolio-dashboard__card ring-0 flex min-h-0 flex-col shadow-none', className)}>
      <CardHeader className="portfolio-dashboard__card-header">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex flex-col gap-0.5">
            <CardTitle className="text-sm">{title}</CardTitle>
            {description ? <CardDescription>{description}</CardDescription> : null}
          </div>
          {headerActions ? (
            <div className="portfolio-dashboard__card-header-actions shrink-0">
              {headerActions}
            </div>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className={cn('portfolio-dashboard__card-content', contentClassName)}>
        {children}
      </CardContent>
    </Card>
  );
}

function BotRankingList({ title, bots, variant = 'up' }) {
  return (
    <Card className="portfolio-dashboard__card ring-0 flex min-h-0 flex-col shadow-none">
      <CardHeader className="portfolio-dashboard__card-header">
        <CardTitle className="flex items-center gap-2 text-sm">
          {title}
          <Badge variant={variant === 'up' ? 'buy' : 'sell'} className="text-[0.62rem]">
            {bots.length}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="portfolio-dashboard__card-content portfolio-dashboard__card-content--list">
        {bots.length ? bots.map((b, i) => (
          <div key={b.bot_id}>
            {i > 0 && <Separator className="my-1.5" />}
            <div className="flex items-center justify-between gap-3 py-1.5">
              <div className="min-w-0 flex flex-col gap-0.5">
                <span className="truncate text-sm font-medium">{b.strategy}</span>
                <span className="truncate text-xs text-muted-foreground">{b.symbol}</span>
              </div>
              <Badge variant={b.total_pnl >= 0 ? 'buy' : 'sell'} className="num-mono shrink-0">
                {fmtUsd(b.total_pnl)}
              </Badge>
            </div>
          </div>
        )) : (
          <Empty className="border-0 py-8">
            <EmptyHeader>
              <EmptyTitle>No bots ranked</EmptyTitle>
              <EmptyDescription>Deploy bots to see performance rankings.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        )}
      </CardContent>
    </Card>
  );
}

export default function PortfolioDashboard({ open = false, onOpenChange }) {
  const [period, setPeriod] = useState('ALL');
  const [source, setSource] = useState('combined');
  const [groupBy, setGroupBy] = useState('strategy');
  const [benchmarks, setBenchmarks] = useState(null);
  const [showSpy, setShowSpy] = useState(true);
  const [showBtc, setShowBtc] = useState(true);
  const [correlationMode, setCorrelationMode] = useState('auto');

  const analyticsBenchmarks = useStore((s) => s.analyticsBenchmarks);
  const tradeHistory = useStore((s) => s.tradeHistory);
  const tradeStats = useStore((s) => s.tradeStats);
  const positions = useStore((s) => s.positions);
  const activeBots = useStore((s) => s.activeBots);
  const symbolsList = useStore((s) => s.symbolsList);
  const bullishColor = useSettingsStore((s) => s.settings.bullishColor ?? '#10b981');
  const bearishColor = useSettingsStore((s) => s.settings.bearishColor ?? '#ef4444');
  const settingsUpdatedAt = useSettingsStore((s) => s.settings.updatedAt ?? '');

  const symbolUniverse = useMemo(
    () => [...new Set((symbolsList || []).filter(Boolean))],
    [symbolsList],
  );

  const params = useMemo(() => ({
    period,
    source,
    group_by: groupBy,
    correlation_mode: correlationMode,
    ...(symbolUniverse.length ? { symbols: symbolUniverse } : {}),
  }), [period, source, groupBy, correlationMode, symbolUniverse]);

  const invalidateKey = useMemo(
    () => buildPortfolioInvalidateKey({
      tradeHistory,
      tradeStats,
      positions,
      activeBots,
      symbolsList: symbolUniverse,
      settingsUpdatedAt,
    }),
    [tradeHistory, tradeStats, positions, activeBots, symbolUniverse, settingsUpdatedAt],
  );

  const { data, loading, error, refresh } = useAnalytics('dashboard', params, { enabled: open, invalidateKey });

  useEffect(() => {
    const onOpen = () => requestAnimationFrame(() => onOpenChange?.(true));
    window.addEventListener('portfolio-dashboard-open', onOpen);
    return () => window.removeEventListener('portfolio-dashboard-open', onOpen);
  }, [onOpenChange]);

  useEffect(() => {
    if (!open) return;
    fetchBenchmarks(period === 'ALL' ? '3mo' : period, ['SPY', 'BTC']);
  }, [open, period]);

  useEffect(() => {
    if (analyticsBenchmarks) setBenchmarks(analyticsBenchmarks);
  }, [analyticsBenchmarks]);

  const onGroupChange = useCallback((g) => {
    setGroupBy(g);
    const payload = { report: 'breakdown', period, source, group_by: g };
    invokeHttpAction(Action.ANALYTICS_GET, payload).catch(() => sendAction(Action.ANALYTICS_GET, payload));
  }, [period, source]);

  const resetKillSwitch = useCallback(async () => {
    try {
      await invokeHttpAction(Action.ADMIN_RESET_RISK_KILL_SWITCH, {});
    } catch {
      await sendAction(Action.ADMIN_RESET_RISK_KILL_SWITCH, {});
    }
    refresh();
  }, [refresh]);

  const equity = data?.equity;
  const stats = equity?.stats;
  const risk = data?.risk;
  const series = equity?.series || [];

  const equityOption = useMemo(() => {
    if (!series.length) return null;
    const cats = series.map((s) => {
      const d = new Date(s.time * 1000);
      return d.toLocaleDateString();
    });
    const lineData = series.map((s) => s.value);
    const isProfit = (stats?.total_pnl ?? 0) >= 0;
    const lineColor = isProfit ? bullishColor : bearishColor;

    const benchSeries = [];
    if (benchmarks?.benchmarks) {
      if (showSpy && benchmarks.benchmarks.SPY?.length) {
        benchSeries.push({
          name: 'SPY %',
          type: 'line',
          data: benchmarks.benchmarks.SPY.slice(-series.length).map((b) => b.value),
          showSymbol: false,
          lineStyle: { color: '#60a5fa', width: 1, type: 'dashed' },
        });
      }
      if (showBtc && benchmarks.benchmarks.BTC?.length) {
        benchSeries.push({
          name: 'BTC %',
          type: 'line',
          data: benchmarks.benchmarks.BTC.slice(-series.length).map((b) => b.value),
          showSymbol: false,
          lineStyle: { color: '#f472b6', width: 1, type: 'dashed' },
        });
      }
    }

    return {
      backgroundColor: 'transparent',
      grid: { left: '2%', right: '4%', top: '12%', bottom: '12%' },
      tooltip: { trigger: 'axis' },
      legend: benchSeries.length ? { data: ['Equity P&L', ...benchSeries.map((s) => s.name)], textStyle: { color: '#9ca3af', fontSize: 10 } } : undefined,
      xAxis: {
        type: 'category',
        data: cats,
        axisLabel: { color: '#6b7280', fontSize: 9 },
      },
      yAxis: [
        {
          type: 'value',
          name: 'P&L $',
          axisLabel: { color: '#6b7280', fontSize: 9, formatter: (v) => `$${v}` },
          splitLine: { lineStyle: { color: 'rgba(255,255,255,0.04)' } },
        },
        benchSeries.length ? {
          type: 'value',
          name: '%',
          axisLabel: { color: '#6b7280', fontSize: 9, formatter: (v) => `${v}%` },
          splitLine: { show: false },
        } : null,
      ].filter(Boolean),
      series: [
        {
          name: 'Equity P&L',
          type: 'line',
          data: lineData,
          showSymbol: false,
          lineStyle: { color: lineColor, width: 2 },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: isProfit ? `${bullishColor}26` : `${bearishColor}26` },
              { offset: 1, color: 'transparent' },
            ]),
          },
        },
        ...benchSeries.map((s) => ({ ...s, yAxisIndex: 1 })),
      ],
    };
  }, [series, stats, benchmarks, showSpy, showBtc, bullishColor, bearishColor]);

  const allocationOption = useMemo(() => {
    const slices = data?.allocation?.by_symbol || [];
    if (!slices.length) return null;
    return {
      backgroundColor: 'transparent',
      tooltip: { trigger: 'item', formatter: '{b}: {c}% ({d}%)' },
      series: [{
        type: 'pie',
        radius: ['42%', '70%'],
        data: slices.map((s) => ({ name: s.symbol, value: s.pct })),
        label: { color: '#9ca3af', fontSize: 9 },
        itemStyle: { borderRadius: 4, borderColor: '#111', borderWidth: 1 },
      }],
    };
  }, [data?.allocation]);

  const riskOption = useMemo(() => {
    if (!risk) return null;
    const ddLimit = risk.max_drawdown_pct ?? 15;
    const ddUtil = ddLimit > 0
      ? Math.min(((risk.current_drawdown_pct ?? 0) / ddLimit) * 100, 100)
      : 0;
    const gauges = [
      ...(risk.kill_switch_enabled ? [{
        name: 'Drawdown',
        value: ddUtil,
        detail: `${(risk.current_drawdown_pct ?? 0).toFixed(1)}%`,
      }] : []),
      { name: 'Gross', value: risk.gross_utilization_pct },
      ...(risk.margin_enabled ? [{
        name: 'Margin',
        value: Math.min(risk.margin_utilization_pct ?? 0, 100),
        detail: `${(risk.margin_utilization_pct ?? 0).toFixed(1)}%`,
      }] : []),
      ...(risk.groups || []).slice(0, 3).map((g) => ({
        name: g.group,
        value: g.utilization_pct,
      })),
    ];
    const count = gauges.length;
    const cols = count <= 2 ? count : 2;
    const rows = Math.ceil(count / cols);

    return {
      backgroundColor: 'transparent',
      series: gauges.map((g, i) => {
        const col = i % cols;
        const row = Math.floor(i / cols);
        return {
          type: 'gauge',
          center: [
            `${((col + 0.5) / cols) * 100}%`,
            `${((row + 0.5) / rows) * 88 + 8}%`,
          ],
          radius: `${Math.min(88 / cols, 78 / rows, 46)}%`,
          min: 0,
          max: 100,
          progress: { show: true, width: 10 },
          axisLine: {
            lineStyle: {
              width: 10,
              color: [[0.7, '#10b981'], [0.9, '#f59e0b'], [1, '#ef4444']],
            },
          },
          axisLabel: { show: false },
          axisTick: { show: false },
          splitLine: { show: false },
          pointer: { show: false },
          detail: {
            formatter: g.detail ? () => g.detail : '{value}%',
            fontSize: 17,
            fontWeight: 600,
            color: '#f3f4f6',
            offsetCenter: [0, '8%'],
          },
          title: {
            fontSize: 12,
            fontWeight: 500,
            color: '#9ca3af',
            offsetCenter: [0, '78%'],
          },
          data: [{ value: Math.min(g.value, 100), name: g.name }],
        };
      }),
    };
  }, [risk]);

  const topBots = data?.bot_rankings?.top || [];
  const bottomBots = data?.bot_rankings?.bottom || [];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        showCloseButton
        overlayClassName="portfolio-dashboard__overlay"
        className={cn(
          'terminal-sheet portfolio-dashboard w-full sm:max-w-none',
          'flex flex-col gap-0 p-0',
        )}
        aria-label="Portfolio Dashboard"
      >
        <SheetHeader className="terminal-sheet__header portfolio-dashboard__header shrink-0">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex min-w-0 flex-col gap-1">
              <SheetTitle className="portfolio-dashboard__title">
                <LayoutDashboard aria-hidden />
                Portfolio Dashboard
                {loading && <Loader2 className="animate-spin text-muted-foreground" aria-hidden />}
              </SheetTitle>
              <SheetDescription className="portfolio-dashboard__description">
                Equity curve, allocation, correlation, bot rankings, and risk analytics
              </SheetDescription>
            </div>
            <div className="portfolio-dashboard__toolbar flex flex-wrap items-center gap-2">
              <ToggleGroup type="single" size="sm" value={period} onValueChange={(v) => v && setPeriod(v)}>
                {ANALYTICS_PERIODS.map((p) => (
                  <ToggleGroupItem key={p.label} value={p.label} className="px-2.5 text-xs">{p.label}</ToggleGroupItem>
                ))}
              </ToggleGroup>
              <Separator orientation="vertical" className="hidden h-6 sm:block" />
              <ToggleGroup type="single" size="sm" value={source} onValueChange={(v) => v && setSource(v)}>
                <ToggleGroupItem value="combined" className="px-2.5 text-xs">Both</ToggleGroupItem>
                <ToggleGroupItem value="bot" className="px-2.5 text-xs">Bots</ToggleGroupItem>
                <ToggleGroupItem value="account" className="px-2.5 text-xs">Manual</ToggleGroupItem>
              </ToggleGroup>
              <Separator orientation="vertical" className="hidden h-6 sm:block" />
              <Button variant={showSpy ? 'secondary' : 'outline'} size="sm" onClick={() => setShowSpy(!showSpy)}>SPY</Button>
              <Button variant={showBtc ? 'secondary' : 'outline'} size="sm" onClick={() => setShowBtc(!showBtc)}>BTC</Button>
              <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>
                <RefreshCw data-icon="inline-start" className={cn(loading && 'animate-spin')} />
                Refresh
              </Button>
            </div>
          </div>
        </SheetHeader>

        <div className="portfolio-dashboard__body terminal-sheet__body">
          {error && (
            <Alert variant="destructive" className="portfolio-dashboard__alert shrink-0">
              <AlertCircle />
              <AlertTitle>Analytics unavailable</AlertTitle>
              <AlertDescription className="flex flex-wrap items-center gap-2">
                {error}
                <Button variant="link" size="sm" className="h-auto p-0" onClick={refresh}>
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          )}

          {risk?.kill_switch_tripped && (
            <Alert variant="destructive" className="portfolio-dashboard__alert shrink-0">
              <ShieldAlert />
              <AlertTitle>Drawdown kill switch tripped</AlertTitle>
              <AlertDescription className="flex flex-wrap items-center gap-2">
                Drawdown {fmtPct(risk.current_drawdown_pct)} exceeded the {fmtPct(risk.max_drawdown_pct)} limit.
                All bots were stopped.
                <Button variant="link" size="sm" className="h-auto p-0" onClick={resetKillSwitch}>
                  Reset kill switch
                </Button>
              </AlertDescription>
            </Alert>
          )}

          {loading && !data ? (
            <Empty className="flex-1 border-0">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <Loader2 className="animate-spin" />
                </EmptyMedia>
                <EmptyTitle>Loading analytics</EmptyTitle>
                <EmptyDescription>Fetching portfolio metrics and chart data…</EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <Tabs defaultValue="overview" className="terminal-tabs portfolio-dashboard__tabs flex min-h-0 flex-1 flex-col">
              <TabsList className="terminal-tabs__list portfolio-dashboard__tablist">
                <TabsTrigger value="overview">Overview</TabsTrigger>
                <TabsTrigger value="breakdown">Breakdown</TabsTrigger>
                <TabsTrigger value="calendar">P&L Calendar</TabsTrigger>
                <TabsTrigger value="exposure">Exposure</TabsTrigger>
                <TabsTrigger value="journal">Journal</TabsTrigger>
              </TabsList>

              <TabsContent value="overview" className="portfolio-dashboard__panel">
                {stats && (
                  <div className="portfolio-dashboard__stats">
                    <StatCard label="Total P&L" icon={TrendingUp} value={fmtUsd(stats.total_pnl)} tone={pnlTone(stats.total_pnl)} />
                    <StatCard label="Win Rate" value={fmtPct(stats.win_rate)} tone={pnlTone(stats.win_rate - 50)} />
                    <StatCard label="Expectancy" value={fmtUsd(stats.expectancy)} tone={pnlTone(stats.expectancy)} />
                    <StatCard label="Trades" icon={BarChart2} value={stats.trade_count} tone="accent" />
                    {risk?.kill_switch_enabled && (
                      <StatCard
                        label="Drawdown"
                        icon={ShieldAlert}
                        value={fmtPct(risk.current_drawdown_pct)}
                        sub={`Peak ${fmtUsd(risk.equity_peak)} · limit ${fmtPct(risk.max_drawdown_pct)}`}
                        tone={pnlTone(-(risk.current_drawdown_pct ?? 0))}
                      />
                    )}
                  </div>
                )}

                <div className="portfolio-dashboard__bento">
                  <ChartCard
                    title="Equity Curve"
                    description="Cumulative P&L with optional SPY and BTC benchmarks"
                    className="portfolio-dashboard__bento-equity"
                    contentClassName="min-h-[260px]"
                  >
                    {equityOption ? (
                      <EChartPanel option={equityOption} className="min-h-[260px] flex-1" deps={[period, source]} />
                    ) : (
                      <Empty className="flex-1 border-0 py-10">
                        <EmptyHeader>
                          <EmptyTitle>No equity data yet</EmptyTitle>
                          <EmptyDescription>Closed trades will populate the equity curve.</EmptyDescription>
                        </EmptyHeader>
                      </Empty>
                    )}
                  </ChartCard>

                  <ChartCard title="Asset Allocation" description="Open position weight by symbol" className="portfolio-dashboard__bento-alloc" contentClassName="min-h-[220px]">
                    {allocationOption ? (
                      <EChartPanel option={allocationOption} className="min-h-[220px] flex-1" />
                    ) : (
                      <Empty className="flex-1 border-0 py-8">
                        <EmptyHeader>
                          <EmptyTitle>No open positions</EmptyTitle>
                          <EmptyDescription>Allocation appears when you hold positions.</EmptyDescription>
                        </EmptyHeader>
                      </Empty>
                    )}
                  </ChartCard>

                  <ChartCard title="Risk Utilization" description="Drawdown vs peak equity, gross and per-group exposure caps" className="portfolio-dashboard__bento-risk" contentClassName="portfolio-dashboard__risk-content">
                    {riskOption ? (
                      <EChartPanel option={riskOption} className="portfolio-dashboard__risk-chart flex-1" />
                    ) : (
                      <Empty className="flex-1 border-0 py-8">
                        <EmptyHeader>
                          <EmptyTitle>Risk data unavailable</EmptyTitle>
                          <EmptyDescription>Configure risk groups to see utilization gauges.</EmptyDescription>
                        </EmptyHeader>
                      </Empty>
                    )}
                  </ChartCard>

                  <ChartCard
                    title="Correlation Matrix"
                    description={
                      data?.correlation?.mode === 'price'
                        ? `Log-return correlation (${data?.correlation?.period || '60d'}, ${data?.correlation?.source || 'yfinance'})`
                        : data?.correlation?.mode === 'trade_pnl'
                          ? 'Pairwise daily return on capital (bot snapshots / normalized PnL)'
                          : 'Auto: price log-returns when dynamic groups enabled'
                    }
                    className="portfolio-dashboard__bento-correlation"
                    contentClassName="min-h-[280px]"
                    headerActions={(
                      <div className="flex flex-wrap items-center justify-end gap-2">
                        <ToggleGroup
                          type="single"
                          value={correlationMode}
                          onValueChange={(v) => { if (v) setCorrelationMode(v); }}
                          size="sm"
                          variant="outline"
                          className="flex-wrap"
                        >
                          <ToggleGroupItem value="auto" className="text-xs px-2">Auto</ToggleGroupItem>
                          <ToggleGroupItem value="price" className="text-xs px-2">Price</ToggleGroupItem>
                          <ToggleGroupItem value="trade_pnl" className="text-xs px-2">Trade PnL</ToggleGroupItem>
                        </ToggleGroup>
                        {data?.correlation?.groups && Object.keys(data.correlation.groups).length ? (
                          <Badge variant="outline" className="text-[0.62rem]">
                            {Object.keys(data.correlation.groups).length} dynamic group(s)
                          </Badge>
                        ) : null}
                      </div>
                    )}
                  >
                    {data?.correlation?.symbols?.length >= 2 && data?.correlation?.matrix?.length ? (
                      <CorrelationMatrix
                        correlation={data.correlation}
                        profitColor={bullishColor}
                        lossColor={bearishColor}
                      />
                    ) : (
                      <Empty className="flex-1 border-0 py-8">
                        <EmptyHeader>
                          <EmptyTitle>Insufficient symbols</EmptyTitle>
                          <EmptyDescription>Need two or more symbols with trades.</EmptyDescription>
                        </EmptyHeader>
                      </Empty>
                    )}
                  </ChartCard>
                </div>

                <div className="portfolio-dashboard__bots">
                  <BotRankingList title="Top Bots" bots={topBots} variant="up" />
                  <BotRankingList title="Bottom Bots" bots={bottomBots} variant="down" />
                </div>
              </TabsContent>

              <TabsContent value="breakdown" className="portfolio-dashboard__panel">
                <StatsBreakdownTable
                  rows={data?.breakdown?.rows || []}
                  groupBy={groupBy}
                  onGroupChange={onGroupChange}
                />
              </TabsContent>

              <TabsContent value="calendar" className="portfolio-dashboard__panel">
                <PnlCalendar
                  days={data?.calendar?.days || []}
                  className="min-h-[280px]"
                  profitColor={bullishColor}
                  lossColor={bearishColor}
                />
              </TabsContent>

              <TabsContent value="exposure" className="portfolio-dashboard__panel">
                <ChartCard
                  title="Exposure Heatmap"
                  description="Open notional concentration by asset class, correlation sector, strategy, or cross-matrix"
                  contentClassName="min-h-[320px]"
                >
                  <ExposureHeatmap
                    exposure={data?.exposure}
                    profitColor={bullishColor}
                    lossColor={bearishColor}
                  />
                </ChartCard>
              </TabsContent>

              <TabsContent value="journal" className="portfolio-dashboard__panel">
                <TradeJournal enabled={open} />
              </TabsContent>
            </Tabs>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
