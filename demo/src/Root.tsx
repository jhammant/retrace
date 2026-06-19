import { Composition } from "remotion";
import { RetraceDemo, DURATION } from "./RetraceDemo";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="RetraceDemo"
      component={RetraceDemo}
      durationInFrames={DURATION}
      fps={30}
      width={1920}
      height={1080}
    />
  );
};
