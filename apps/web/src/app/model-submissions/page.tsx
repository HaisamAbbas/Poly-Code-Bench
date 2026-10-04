import type { Metadata } from "next";
import { PageIntro, ReleaseNotice } from "@/components/public-ui";
import { ModelSubmissionForm } from "@/components/model-submission-form";
import { loadReleaseContext } from "@/lib/public-api";

export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  title: "Submit a model",
  description: "Request a reviewed, explicitly bounded model evaluation.",
};

export default async function ModelSubmissionsPage() {
  const release = await loadReleaseContext();
  return (
    <>
      <PageIntro
        eyebrow="Reviewed requests"
        title="Request a model evaluation"
        description="Submit model and source details for review. A request stays pending until an authorized administrator verifies the source, endpoint, model configuration and a fixed run budget."
      />
      {release.state === "ready" ? (
        <section className="submission-release" aria-labelledby="submission-release-heading">
          <h2 id="submission-release-heading">Current published release context</h2>
          <ReleaseNotice release={release.value.summary} />
        </section>
      ) : (
        <section className="submission-release" role={release.state === "error" ? "status" : undefined}>
          <h2>Published release context</h2>
          <p>
            {release.state === "empty"
              ? release.message
              : release.state === "error"
                ? `Release details are temporarily unavailable: ${release.message}`
                : "Loading release information."}
          </p>
        </section>
      )}
      <ModelSubmissionForm />
    </>
  );
}
