import type { Metadata } from "next";
import { cookies } from "next/headers";
import { PageIntro, ReleaseNotice } from "@/components/public-ui";
import { ModelSubmissionForm } from "@/components/model-submission-form";
import { oidcConfigured, readSessionToken, sessionCookieName } from "@/lib/oidc-auth";
import { loadReleaseContext } from "@/lib/public-api";

export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  title: "Submit a model",
  description: "Request a reviewed, explicitly bounded model evaluation.",
};

export default async function ModelSubmissionsPage({
  searchParams,
}: {
  readonly searchParams?: Promise<{ readonly auth_error?: string }>;
}) {
  const params = await searchParams;
  const release = await loadReleaseContext();
  const loginAvailable = oidcConfigured();
  const cookieStore = loginAvailable ? await cookies() : null;
  const session = cookieStore ? readSessionToken(cookieStore.get(sessionCookieName())?.value) : null;
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
      {params?.auth_error === "sign_in_failed" ? (
        <p className="submission-error" role="alert">Sign-in was not completed. Use an account with a verified email, then try again.</p>
      ) : null}
      <ModelSubmissionForm identity={session ? { email: session.email } : null} loginAvailable={loginAvailable} />
    </>
  );
}
