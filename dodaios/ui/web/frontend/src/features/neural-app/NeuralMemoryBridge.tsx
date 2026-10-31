import { NeuralApp, type MemoryNode } from "./NeuralApp";
import { useNeuralMemory, type NeuralMemoryEvent } from "./useNeuralMemory";

export type MemoryBridgeProps = {
  product: "consumer" | "work";
  accountId: string;
  subscribe: (listener: (event: NeuralMemoryEvent) => void) => () => void;
  onOpenMemory?: (id: string) => void;
  onStartVoice?: () => void;
  onStopVoice?: () => void;
  listening?: boolean;
};

/** Separate opt-in host adapter; never opens a network or database connection. */
export function NeuralMemoryBridge({product,accountId,subscribe,onOpenMemory,
  onStartVoice,onStopVoice,listening}:MemoryBridgeProps) {
  const {nodes}=useNeuralMemory(product,accountId,subscribe);
  return <NeuralApp memories={nodes as MemoryNode[]} onOpenMemory={onOpenMemory}
    onStartVoice={onStartVoice} onStopVoice={onStopVoice} listening={listening}/>;
}
