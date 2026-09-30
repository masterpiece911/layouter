import { defineWorkflow, Container, Window, Workspace } from '../src/index.js';
const numberedWorkspace = <Workspace number={2} />;
// @ts-expect-error a workspace needs a name or a number
const missingWorkspaceDestination = <Workspace />;
const workflow = defineWorkflow({
  description: "Open development tools for the selected environment",
  args: { environment: { position: 0, choices: ['dev', 'prod'], default: 'dev' } },
  component({ args }) {
    const environment: 'dev' | 'prod' = args.environment;
    // @ts-expect-error unknown argument
    args.missing;
    // @ts-expect-error inferred choice union excludes arbitrary strings
    const invalid: 'staging' = args.environment;
    return <Window name={environment} command={['app']} />;
  },
});
// @ts-expect-error stable structural names are mandatory
const unnamed = <Container layout="splith" />;
// @ts-expect-error geometry requires floating
const tiled = <Window name="w" command={['app']} width={100} />;
// @ts-expect-error named placement and pixel position are mutually exclusive
const conflicting = <Window name="w" command={['app']} floating position="center" x={10} />;
const floating = <Window name="w" command={['app']} floating position="center" width={100} />;

// @ts-expect-error description must be a string
defineWorkflow({ description: 42, component() { return null; } });

import { FirefoxWindow, FirefoxTab } from '../src/index.js';
const browserWindow = <FirefoxWindow name="browser" args={["-P", "Work"]}>
  <FirefoxTab url="https://example.com" pinned active />
</FirefoxWindow>;
// @ts-expect-error Browser tabs require a URL.
const missingUrl = <FirefoxTab name="tab" />;
// @ts-expect-error Firefox windows have no adoption API.
const adoptedBrowser = <FirefoxWindow name="browser" adopt />;
