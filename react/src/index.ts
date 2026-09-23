import type { ComponentType } from 'react';
export * from './types.js';
export * from './primitives.js';
export { useLayouter, useOutputs, usePrimaryOutput, useOutput, useScreen,
         useWorkflowArgs, useArg } from './context.js';

export interface ArgumentDeclaration {
  position: number;
  required?: boolean;
  default?: string;
  choices?: readonly string[];
  help?: string;
}
export type ArgumentDeclarations = Record<string, ArgumentDeclaration>;
export type BoundArgs<A extends ArgumentDeclarations> = {
  [K in keyof A]: A[K] extends { choices: readonly (infer C extends string)[] } ? C : string
};
export interface WorkflowDefinition<A extends ArgumentDeclarations = ArgumentDeclarations> {
  args?: A;
  component: ComponentType<{ args: BoundArgs<A> }>;
}
/** Metadata is consumed by Python before React runs. No JS argument binder. */
export function defineWorkflow<const A extends ArgumentDeclarations = {}>(
  definition: WorkflowDefinition<A>,
): WorkflowDefinition<A> {
  return definition;
}
