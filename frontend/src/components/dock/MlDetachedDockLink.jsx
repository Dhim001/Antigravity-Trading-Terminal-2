import DetachedDockLink from './DetachedDockLink';

/** @deprecated Prefer DetachedDockLink with panelId="ml-lab". */
export default function MlDetachedDockLink({ onAttach }) {
  return <DetachedDockLink panelId="ml-lab" onAttach={onAttach} title="ML Lab" />;
}
