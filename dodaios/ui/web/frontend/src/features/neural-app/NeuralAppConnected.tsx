import { NeuralApp, type MemoryNode } from "./NeuralApp";
import { useNeuralDashboard } from "./useNeuralDashboard";
import { useNeuralMicrophone } from "./useNeuralMicrophone";

/** Explicit integration boundary: nothing is sent to the network by this module. */
export type NeuralAppConnectedProps = {
  product: "consumer" | "work";
  accountId: string;
  memories?: MemoryNode[];
  onVoiceRecording?: (audio: Blob) => Promise<void> | void;
  onOpenWidget?: (id: string) => void;
  onOpenMemory?: (id: string) => void;
};
export function NeuralAppConnected({ product, accountId, memories = [],
  onVoiceRecording, onOpenWidget, onOpenMemory }: NeuralAppConnectedProps) {
  const dashboard = useNeuralDashboard(product, accountId);
  const voice = useNeuralMicrophone(onVoiceRecording);
  return <>
    <NeuralApp
      memories={memories} widgets={dashboard.widgets}
      listening={voice.listening}
      busy={voice.state === "requesting" || voice.state === "stopping"}
      onStartVoice={voice.start}
      onStopVoice={voice.stop}
      onOpenWidget={onOpenWidget}
      onOpenMemory={onOpenMemory}
      onAddWidget={dashboard.add}
      onRemoveWidget={dashboard.remove}
    />
    {voice.error && <div role="alert" style={{ position: "fixed", bottom: 92, left: 12,
      right: 12, background: "#40131b", color: "white", padding: 12,
      borderRadius: 12, zIndex: 50 }}>{voice.error}</div>}
  </>;
}
