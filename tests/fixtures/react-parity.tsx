import { defineWorkflow, Workflow, Workspace, Container, Window, Kitty, Tab, Pane } from '@layouter/react';
export default defineWorkflow({
  args: { mode: { position: 0, choices: ['dev', 'prod'], default: 'dev', help: 'Environment' } },
  component({ args }) {
    return <Workflow session="parity-{mode}" cwd="src" env={{ ROOT: 'root', MODE: args.mode }} focus="tabs.main.shell" syncDisplays>
      <Workspace name="code" number={2} layout="splith" output={['DP-1', 'eDP-1']}>
        <Window name="editor" command={['zed', '{project}']} env={{ EDITOR: 'yes' }} />
        <Kitty name="inline" layout="tall" cwd="tools" env={{ CHILD: 'yes' }}>
          <Pane name="server" command={['echo', '{mode}']} />
          <Pane name="disabled-pane" enabled={false} />
        </Kitty>
        <Container name="nested" layout="splitv" size={30}>
          <Window name="browser" command={['firefox']} />
          <Kitty name="tabs" env={{ TERM_ENV: 'term' }}>
            <Tab name="main" layout="splits" cwd="tabs" env={{ TAB_ENV: 'tab' }}>
              <Pane name="shell" command={['sh']} env={{ PANE_ENV: 'pane' }} />
              <Pane name="logs" after="shell" location="vsplit" />
            </Tab>
          </Kitty>
          <Window name="last" command={['last']} />
        </Container>
        <Window name="disabled" enabled={false} command={['disabled']} />
        <Container name="disabled-container" layout="splith" enabled={false}>
          <Window name="disabled-descendant" command={['disabled']} />
        </Container>
        <Window name="floating-window" command={['app']} floating x={10} y={20} width={500} height={400} />
        <Kitty name="floating-kitty" floating position="center" width={1000} height={700}>
          <Pane name="shell" />
        </Kitty>
      </Workspace>
    </Workflow>;
  },
});
