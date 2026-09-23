import { defineWorkflow, Container, Window } from '../src/index.js';
const workflow = defineWorkflow({
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
