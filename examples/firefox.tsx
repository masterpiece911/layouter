import { defineWorkflow, Workflow, Workspace, FirefoxWindow, FirefoxTab } from '@layouter/react';

export default defineWorkflow({
  component: () => (
    <Workflow session="dev">
      <Workspace number={3}>
        <FirefoxWindow name="docs" executable="firefox" args={["-P", "Work"]}>
          <FirefoxTab url="https://developer.mozilla.org/" pinned />
        </FirefoxWindow>
      </Workspace>
      <Workspace number={4}>
        <FirefoxWindow name="project" executable="firefox" args={["-P", "Work"]}>
          <FirefoxTab name="repo" url="https://github.com/masterpiece911/layouter" active />
        </FirefoxWindow>
      </Workspace>
    </Workflow>
  ),
});
