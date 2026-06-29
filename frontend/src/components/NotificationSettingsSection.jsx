import React, { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Bell, Plus, Trash2, Send, RefreshCw, Mail, Smartphone } from 'lucide-react';
import { Action } from '../api/protocol';
import { invokeHttpAction } from '../api/transport';
import { pushSupported, subscribeBrowserPush, unsubscribeBrowserPush } from '@/lib/pushNotifications';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

const REALTIME_EVENTS = [
  { id: 'trade_fill', label: 'Trade fills' },
  { id: 'sl_tp_trigger', label: 'SL / TP triggers' },
  { id: 'bot_status', label: 'Bot status changes' },
  { id: 'bot_log_warn', label: 'Bot warnings' },
  { id: 'bot_log_error', label: 'Bot errors' },
  { id: 'kill_switch', label: 'Drawdown kill switch' },
  { id: 'safe_mode', label: 'Safe mode' },
  { id: 'emergency_stop', label: 'Emergency stop' },
  { id: 'alert_rule', label: 'Alert rules' },
];

const DIGEST_EVENT = { id: 'daily_digest', label: 'Daily digest' };

const CHANNEL_TYPES = [
  { id: 'webhook', label: 'Webhook' },
  { id: 'telegram', label: 'Telegram' },
  { id: 'email', label: 'Email (SMTP)' },
  { id: 'push', label: 'Browser push' },
];

const defaultEventTypes = (channelType) => {
  if (channelType === 'email') return [DIGEST_EVENT.id];
  if (channelType === 'push') return REALTIME_EVENTS.map((e) => e.id);
  return [...REALTIME_EVENTS.map((e) => e.id), DIGEST_EVENT.id];
};

const EMPTY_FORM = {
  id: null,
  channel_type: 'webhook',
  name: '',
  url: '',
  preset: 'slack',
  hmac_secret: '',
  bot_token: '',
  chat_id: '',
  parse_mode: 'MarkdownV2',
  smtp_host: '',
  smtp_port: '587',
  smtp_user: '',
  smtp_password: '',
  from_address: '',
  to_addresses: '',
  use_tls: true,
  enabled: true,
  event_types: defaultEventTypes('webhook'),
  rotate_subscribe_secret: false,
};

function channelSummary(ch) {
  if (ch.channel_type === 'telegram') {
    return ch.chat_id_masked || ch.bot_token_masked || '—';
  }
  if (ch.channel_type === 'email') {
    const to = (ch.to_addresses || []).join(', ');
    return `${ch.smtp_host || ''} → ${to || '—'}`;
  }
  if (ch.channel_type === 'push') {
    const n = ch.subscription_count ?? 0;
    return `${n} browser${n === 1 ? '' : 's'} subscribed`;
  }
  return ch.url_masked || '—';
}

function channelBadge(ch) {
  if (ch.channel_type === 'telegram') return 'Telegram';
  if (ch.channel_type === 'email') return 'Email';
  if (ch.channel_type === 'push') return 'Push';
  return ch.preset || 'webhook';
}

export default function NotificationSettingsSection() {
  const [channels, setChannels] = useState([]);
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editing, setEditing] = useState(false);
  const [sendingDigest, setSendingDigest] = useState(false);
  const [webPushReady, setWebPushReady] = useState(false);
  const [subscribingPush, setSubscribingPush] = useState(false);

  const loadChannels = useCallback(async () => {
    setLoading(true);
    try {
      const [res, vapidRes] = await Promise.all([
        invokeHttpAction(Action.NOTIFY_CHANNEL_LIST, {}),
        invokeHttpAction(Action.NOTIFY_PUSH_VAPID_PUBLIC, {}).catch(() => null),
      ]);
      setChannels(res?.data?.notification_channels ?? []);
      setWebPushReady(Boolean(vapidRes?.data?.web_push_enabled && vapidRes?.data?.vapid_public_key));
    } catch (err) {
      toast.error(err?.message || 'Failed to load notification channels');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadChannels();
  }, [loadChannels]);

  const eventOptions = form.channel_type === 'email'
    ? [DIGEST_EVENT, ...REALTIME_EVENTS]
    : form.channel_type === 'push'
      ? REALTIME_EVENTS
      : [...REALTIME_EVENTS, DIGEST_EVENT];

  const pushSecretKey = (channelId) => `push_subscribe_secret:${channelId}`;

  const storePushSubscribeSecret = (channelId, secret) => {
    if (channelId && secret) {
      try {
        localStorage.setItem(pushSecretKey(channelId), secret);
      } catch {
        /* ignore quota errors */
      }
    }
  };

  const getPushSubscribeSecret = (channelId) => {
    try {
      return localStorage.getItem(pushSecretKey(channelId)) || '';
    } catch {
      return '';
    }
  };

  const toggleEventType = (id) => {
    setForm((f) => {
      const set = new Set(f.event_types);
      if (set.has(id)) set.delete(id);
      else set.add(id);
      return { ...f, event_types: [...set] };
    });
  };

  const onChannelTypeChange = (channelType) => {
    setForm((f) => ({
      ...f,
      channel_type: channelType,
      event_types: editing ? f.event_types : defaultEventTypes(channelType),
    }));
  };

  const validateForm = () => {
    if (!form.name.trim()) {
      toast.error('Name is required');
      return false;
    }
    if (form.channel_type === 'webhook' && !form.id && !form.url.trim()) {
      toast.error('Webhook URL is required');
      return false;
    }
    if (form.channel_type === 'telegram') {
      if (!form.id && !form.bot_token.trim()) {
        toast.error('Bot token is required');
        return false;
      }
      if (!form.id && !form.chat_id.trim()) {
        toast.error('Chat ID is required');
        return false;
      }
    }
    if (form.channel_type === 'email') {
      if (!form.smtp_host.trim()) {
        toast.error('SMTP host is required');
        return false;
      }
      if (!form.to_addresses.trim()) {
        toast.error('Recipient email(s) required');
        return false;
      }
    }
    return true;
  };

  const buildPayload = () => {
    const base = {
      id: form.id,
      channel_type: form.channel_type,
      name: form.name.trim(),
      enabled: form.enabled,
      event_types: form.event_types,
    };
    if (form.channel_type === 'webhook') {
      return {
        ...base,
        ...(form.url.trim() ? { url: form.url.trim() } : {}),
        preset: form.preset,
        ...(form.hmac_secret ? { hmac_secret: form.hmac_secret } : {}),
      };
    }
    if (form.channel_type === 'telegram') {
      return {
        ...base,
        ...(form.bot_token.trim() ? { bot_token: form.bot_token.trim() } : {}),
        ...(form.chat_id.trim() ? { chat_id: form.chat_id.trim() } : {}),
        parse_mode: form.parse_mode,
      };
    }
    if (form.channel_type === 'push') {
      return {
        ...base,
        ...(form.rotate_subscribe_secret ? { rotate_subscribe_secret: true } : {}),
      };
    }
    return {
      ...base,
      smtp_host: form.smtp_host.trim(),
      smtp_port: parseInt(form.smtp_port, 10) || 587,
      smtp_user: form.smtp_user.trim(),
      from_address: form.from_address.trim() || form.smtp_user.trim(),
      to_addresses: form.to_addresses.split(',').map((s) => s.trim()).filter(Boolean),
      use_tls: form.use_tls,
      ...(form.smtp_password ? { smtp_password: form.smtp_password } : {}),
    };
  };

  const enablePushOnBrowser = async (channelId) => {
    if (!pushSupported()) {
      toast.error('Push notifications are not supported in this browser');
      return;
    }
    setSubscribingPush(true);
    try {
      const vapidRes = await invokeHttpAction(Action.NOTIFY_PUSH_VAPID_PUBLIC, {});
      const key = vapidRes?.data?.vapid_public_key;
      if (!key) {
        toast.error(vapidRes?.data?.message || 'Web Push not configured on server');
        return;
      }
      const sub = await subscribeBrowserPush(key);
      const subscribeSecret = getPushSubscribeSecret(channelId);
      if (!subscribeSecret) {
        toast.error('Save the push channel first to obtain a subscribe key');
        return;
      }
      const res = await invokeHttpAction(Action.NOTIFY_PUSH_SUBSCRIBE, {
        channel_id: channelId,
        subscription: sub.subscription,
        subscribe_secret: subscribeSecret,
        user_agent: navigator.userAgent,
      });
      if (res?.data?.status === 'error') {
        toast.error(res.data.message || 'Subscribe failed');
        return;
      }
      toast.success('Browser push enabled on this device');
      loadChannels();
    } catch (err) {
      toast.error(err?.message || 'Failed to enable push');
    } finally {
      setSubscribingPush(false);
    }
  };

  const disablePushOnBrowser = async (channelId) => {
    try {
      const endpoint = await unsubscribeBrowserPush();
      if (endpoint) {
        await invokeHttpAction(Action.NOTIFY_PUSH_UNSUBSCRIBE, { endpoint });
      }
      toast.success('Browser push disabled on this device');
      loadChannels();
    } catch (err) {
      toast.error(err?.message || 'Failed to disable push');
    }
  };

  const saveChannel = async () => {
    if (!validateForm()) return;
    try {
      const res = await invokeHttpAction(Action.NOTIFY_CHANNEL_UPSERT, buildPayload());
      if (res?.data?.status === 'error') {
        toast.error(res.data.message || 'Save failed');
        return;
      }
      const saved = res?.data?.notification_channel;
      const secret = res?.data?.subscribe_secret;
      if (saved?.id && secret) {
        storePushSubscribeSecret(saved.id, secret);
      }
      toast.success('Channel saved');
      setForm(EMPTY_FORM);
      setEditing(false);
      loadChannels();
    } catch (err) {
      toast.error(err?.message || 'Save failed');
    }
  };

  const testChannel = async (id) => {
    try {
      const res = await invokeHttpAction(Action.NOTIFY_CHANNEL_TEST, { id });
      if (res?.data?.status === 'error') {
        toast.error(res.data.message || 'Test failed');
        return;
      }
      toast.success('Test notification sent');
    } catch (err) {
      toast.error(err?.message || 'Test failed');
    }
  };

  const sendDigestNow = async () => {
    setSendingDigest(true);
    try {
      const res = await invokeHttpAction(Action.NOTIFY_DIGEST_SEND_NOW, {});
      if (res?.data?.status === 'error') {
        toast.error(res.data.message || 'Digest send failed');
        return;
      }
      toast.success(res?.data?.message || 'Digest sent');
    } catch (err) {
      toast.error(err?.message || 'Digest send failed');
    } finally {
      setSendingDigest(false);
    }
  };

  const deleteChannel = async (id) => {
    try {
      await invokeHttpAction(Action.NOTIFY_CHANNEL_DELETE, { id });
      toast.success('Channel deleted');
      if (form.id === id) {
        setForm(EMPTY_FORM);
        setEditing(false);
      }
      loadChannels();
    } catch (err) {
      toast.error(err?.message || 'Delete failed');
    }
  };

  const startEdit = (ch) => {
    setEditing(true);
    setForm({
      ...EMPTY_FORM,
      id: ch.id,
      channel_type: ch.channel_type || 'webhook',
      name: ch.name,
      preset: ch.preset || 'generic',
      parse_mode: ch.parse_mode || 'MarkdownV2',
      smtp_host: ch.smtp_host || '',
      smtp_port: String(ch.smtp_port || 587),
      from_address: ch.from_address || '',
      use_tls: ch.use_tls !== false,
      enabled: ch.enabled,
      event_types: ch.event_types?.length ? ch.event_types : defaultEventTypes(ch.channel_type || 'webhook'),
    });
  };

  const typeLabel = CHANNEL_TYPES.find((t) => t.id === form.channel_type)?.label || 'Channel';

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground max-w-xl">
          Webhooks, Telegram, email digest, and browser push. Secrets are encrypted in the database.
        </p>
        <div className="flex gap-1">
          <Button
            variant="outline"
            size="xs"
            onClick={sendDigestNow}
            disabled={sendingDigest}
          >
            <Mail data-icon="inline-start" aria-hidden />
            Send digest now
          </Button>
          <Button variant="ghost" size="xs" onClick={loadChannels} disabled={loading}>
            <RefreshCw data-icon="inline-start" className={loading ? 'animate-spin' : ''} aria-hidden />
            Refresh
          </Button>
        </div>
      </div>

      {channels.length > 0 && (
        <ul className="space-y-2">
          {channels.map((ch) => (
            <li
              key={ch.id}
              className="flex flex-wrap items-center gap-2 rounded-md border border-border/60 bg-muted/20 px-3 py-2 text-xs"
            >
              <Bell size={14} className="shrink-0 text-muted-foreground" aria-hidden />
              <span className="font-medium">{ch.name}</span>
              <Badge variant="outline" className="text-[10px] capitalize">{channelBadge(ch)}</Badge>
              {!ch.enabled && <Badge variant="secondary">Disabled</Badge>}
              <span className="text-muted-foreground font-mono truncate max-w-[220px]">
                {channelSummary(ch)}
              </span>
              <span className="flex-1" />
              {ch.channel_type === 'push' && webPushReady && pushSupported() && (
                <>
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={subscribingPush}
                    onClick={() => enablePushOnBrowser(ch.id)}
                  >
                    <Smartphone data-icon="inline-start" aria-hidden />
                    Enable here
                  </Button>
                  <Button variant="ghost" size="xs" onClick={() => disablePushOnBrowser(ch.id)}>
                    Disable here
                  </Button>
                </>
              )}
              <Button variant="ghost" size="xs" onClick={() => startEdit(ch)}>Edit</Button>
              <Button variant="ghost" size="xs" onClick={() => testChannel(ch.id)}>
                <Send data-icon="inline-start" aria-hidden />
                Test
              </Button>
              <Button variant="ghost" size="xs" onClick={() => deleteChannel(ch.id)}>
                <Trash2 data-icon="inline-start" aria-hidden />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <div className="rounded-md border border-border/60 p-3 space-y-3">
        <div className="flex items-center gap-2">
          <Plus size={14} aria-hidden />
          <span className="text-sm font-medium">{editing ? `Edit ${typeLabel}` : 'Add channel'}</span>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label className="text-xs">Channel type</Label>
            <Select
              value={form.channel_type}
              onValueChange={onChannelTypeChange}
              disabled={editing}
            >
              <SelectTrigger className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CHANNEL_TYPES.map((t) => (
                  <SelectItem key={t.id} value={t.id}>{t.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="notify-name" className="text-xs">Name</Label>
            <Input
              id="notify-name"
              className="h-8 text-xs"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="Alerts"
            />
          </div>
        </div>

        {form.channel_type === 'webhook' && (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs">Preset</Label>
                <Select value={form.preset} onValueChange={(v) => setForm((f) => ({ ...f, preset: v }))}>
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="slack">Slack</SelectItem>
                    <SelectItem value="discord">Discord</SelectItem>
                    <SelectItem value="generic">Generic JSON</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="notify-url" className="text-xs">Webhook URL</Label>
              <Input
                id="notify-url"
                className="h-8 text-xs font-mono"
                value={form.url}
                onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
                placeholder={editing ? 'Leave blank to keep existing URL' : 'https://hooks.slack.com/...'}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="notify-hmac" className="text-xs">HMAC secret (optional)</Label>
              <Input
                id="notify-hmac"
                type="password"
                className="h-8 text-xs font-mono"
                value={form.hmac_secret}
                onChange={(e) => setForm((f) => ({ ...f, hmac_secret: e.target.value }))}
                placeholder="X-Terminal-Signature header"
              />
            </div>
          </>
        )}

        {form.channel_type === 'telegram' && (
          <>
            <div className="space-y-1.5">
              <Label htmlFor="notify-bot-token" className="text-xs">Bot token</Label>
              <Input
                id="notify-bot-token"
                type="password"
                className="h-8 text-xs font-mono"
                value={form.bot_token}
                onChange={(e) => setForm((f) => ({ ...f, bot_token: e.target.value }))}
                placeholder={editing ? 'Leave blank to keep existing token' : '123456:ABC...'}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="notify-chat-id" className="text-xs">Chat ID</Label>
              <Input
                id="notify-chat-id"
                className="h-8 text-xs font-mono"
                value={form.chat_id}
                onChange={(e) => setForm((f) => ({ ...f, chat_id: e.target.value }))}
                placeholder={editing ? 'Leave blank to keep existing chat ID' : '-1001234567890'}
              />
            </div>
          </>
        )}

        {form.channel_type === 'email' && (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs">SMTP host</Label>
                <Input
                  className="h-8 text-xs font-mono"
                  value={form.smtp_host}
                  onChange={(e) => setForm((f) => ({ ...f, smtp_host: e.target.value }))}
                  placeholder="smtp.gmail.com"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">SMTP port</Label>
                <Input
                  className="h-8 text-xs font-mono"
                  value={form.smtp_port}
                  onChange={(e) => setForm((f) => ({ ...f, smtp_port: e.target.value }))}
                  placeholder="587"
                />
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs">SMTP user</Label>
                <Input
                  className="h-8 text-xs font-mono"
                  value={form.smtp_user}
                  onChange={(e) => setForm((f) => ({ ...f, smtp_user: e.target.value }))}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">SMTP password</Label>
                <Input
                  type="password"
                  className="h-8 text-xs font-mono"
                  value={form.smtp_password}
                  onChange={(e) => setForm((f) => ({ ...f, smtp_password: e.target.value }))}
                  placeholder={editing ? 'Leave blank to keep existing' : ''}
                />
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs">From address</Label>
                <Input
                  className="h-8 text-xs font-mono"
                  value={form.from_address}
                  onChange={(e) => setForm((f) => ({ ...f, from_address: e.target.value }))}
                  placeholder="Optional — defaults to SMTP user"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">To address(es)</Label>
                <Input
                  className="h-8 text-xs font-mono"
                  value={form.to_addresses}
                  onChange={(e) => setForm((f) => ({ ...f, to_addresses: e.target.value }))}
                  placeholder="you@example.com, ops@example.com"
                />
              </div>
            </div>
            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <Checkbox
                checked={form.use_tls}
                onCheckedChange={(v) => setForm((f) => ({ ...f, use_tls: Boolean(v) }))}
              />
              Use STARTTLS
            </label>
          </>
        )}

        {form.channel_type === 'push' && (
          <>
            <p className="text-xs text-muted-foreground">
              {webPushReady
                ? 'Save the channel, then click “Enable here” on the channel row to register this browser. Requires HTTPS or localhost.'
                : 'Set VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY on the backend (see .env.example).'}
            </p>
            {editing && (
              <label className="flex items-center gap-2 text-xs cursor-pointer">
                <Checkbox
                  checked={form.rotate_subscribe_secret}
                  onCheckedChange={(v) => setForm((f) => ({ ...f, rotate_subscribe_secret: Boolean(v) }))}
                />
                Regenerate subscribe key (required if this browser lost its key)
              </label>
            )}
          </>
        )}

        <div className="space-y-2">
          <Label className="text-xs">Event types</Label>
          <div className="flex flex-wrap gap-2">
            {eventOptions.map((et) => (
              <label key={et.id} className="flex items-center gap-1.5 text-xs cursor-pointer">
                <Checkbox
                  checked={form.event_types.includes(et.id)}
                  onCheckedChange={() => toggleEventType(et.id)}
                />
                {et.label}
              </label>
            ))}
          </div>
        </div>
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <Checkbox
            checked={form.enabled}
            onCheckedChange={(v) => setForm((f) => ({ ...f, enabled: Boolean(v) }))}
          />
          Enabled
        </label>
        <div className="flex gap-2">
          <Button size="sm" onClick={saveChannel}>{editing ? 'Update' : 'Add'} channel</Button>
          {editing && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => { setForm(EMPTY_FORM); setEditing(false); }}
            >
              Cancel
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
