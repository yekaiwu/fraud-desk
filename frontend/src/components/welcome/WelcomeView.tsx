import { CaseTypes } from "./CaseTypes";
import { Closing, Footer } from "./Closing";
import { Explainable } from "./Explainable";
import { Flow } from "./Flow";
import { Hero } from "./Hero";
import { Limits } from "./Limits";
import { Officer } from "./Officer";
import { Section, SectionHead } from "./parts";
import { ScoreCalculator } from "./ScoreCalculator";
import { Silence } from "./Silence";
import { SpotTheEdit } from "./SpotTheEdit";
import { TopBar } from "./TopBar";

/**
 * The public product tour. Server-rendered end to end except the two demos and the score explainer (client islands).
 * Makes no API calls and loads no external resources: all content and sample data are in the bundle.
 */
export function WelcomeView({ signedIn }: { signedIn: boolean }) {
  return (
    <div className="welcome-root">
      <TopBar signedIn={signedIn} />
      <main id="main" tabIndex={-1} className="focus:outline-none">
        <Hero signedIn={signedIn} />
        <Flow />
        <Section id="try" tone="dark" labelledBy="try-title">
          <SectionHead
            dark
            no="03"
            label="Try it"
            id="try-title"
            title="Spot the edit."
            intro="This is a fictitious bank statement. Someone has changed one figure. Run the checks and watch the desk find it, show its working, and score it."
          />
          <SpotTheEdit />
        </Section>
        <Section id="score" labelledBy="score-title">
          <SectionHead
            no="04"
            label="How the score works"
            id="score-title"
            title="A score you can add up yourself."
            intro="Points per finding, by how serious it is. Add them, stop at 100, read the band. There is nothing else in the number."
          />
          <ScoreCalculator />
        </Section>
        <Silence />
        <CaseTypes />
        <Officer />
        <Explainable />
        <Limits />
        <Closing signedIn={signedIn} />
      </main>
      <Footer />
    </div>
  );
}
