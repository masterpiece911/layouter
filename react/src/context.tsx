import React, { createContext, useContext } from "react";
import type { EvaluationContext, Output } from "./types.js";

const Context = createContext<EvaluationContext | null>(null);

export function EvaluationContextProvider({
  value,
  children,
}: {
  value: EvaluationContext;
  children: React.ReactNode;
}) {
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useLayouter(): EvaluationContext {
  const value = useContext(Context);
  if (!value) {
    throw new Error("Layouter hooks must be used while evaluating a workflow");
  }
  return value;
}

export function useOutputs(): Output[] {
  return useLayouter().outputs ?? [];
}

export function usePrimaryOutput(): Output | undefined {
  const outputs = useOutputs().filter((output) => output.active);
  return outputs.find((output) => output.primary) ?? outputs[0];
}

export function useOutput(name: string): Output | undefined {
  return useOutputs().find((output) => output.active && output.name === name);
}

export function useScreen(name?: string) {
  const outputs = useOutputs().filter(output => output.active);
  const output = name ? outputs.find(output => output.name === name)
    : outputs.find(output => output.primary) ?? outputs[0];

  return {
    output,
    width: output?.rect.width ?? 0,
    height: output?.rect.height ?? 0,
    scale: output?.scale ?? 1,
  };
}

export function useWorkflowArgs(): Readonly<Record<string, string>> {
  return useLayouter().args;
}

export function useArg(name: string): string {
  const args = useWorkflowArgs();
  if (!Object.hasOwn(args, name)) throw new Error(`Unknown workflow argument: ${name}`);
  return args[name];
}
