import { defineWorkflow, Workflow, Workspace, Window, Kitty, Pane,
         useArg, usePrimaryOutput } from '@layouter/react';

function DevTools() {
  const microfrontend = useArg('microfrontend');
  return <Kitty name="dev" layout="tall">
    <Pane name="server" command={['npm', 'run', 'dev', '--', microfrontend]} />
    <Pane name="tests" command={['npm', 'test', '--', '--watch']} />
  </Kitty>;
}

export default defineWorkflow({
  args: {
    microfrontend: { position: 0, required: true, help: 'Microfrontend to launch' },
    environment: { position: 1, default: 'dev', choices: ['dev', 'prod'], help: 'Target environment' },
  },
  component({ args }) {
    const screen = usePrimaryOutput();
    return <Workflow session={`dev-${args.microfrontend}-${args.environment}`} focus="code">
      <Workspace name="code" number={2} layout={(screen?.rect.width ?? 0) >= 1920 ? 'splith' : 'splitv'}>
        <Window name="editor" command={['zed', '.']} />
        <DevTools />
        <Kitty name="scratch" floating position="center" width={1000} height={700}>
          <Pane name="shell" />
        </Kitty>
      </Workspace>
    </Workflow>;
  },
});
