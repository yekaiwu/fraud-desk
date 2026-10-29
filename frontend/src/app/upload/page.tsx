import { SectionHeading, Stagger } from "@/components/ui";
import { UploadForm } from "@/components/upload/UploadForm";

export const metadata = { title: "New case" };

export default function UploadPage() {
  return (
    <Stagger className="space-y-6">
      <SectionHeading level={1} size="page" description="Add the customer's documents. We read them, run the checks and rank the case.">
        New case
      </SectionHeading>
      <UploadForm />
    </Stagger>
  );
}
