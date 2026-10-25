// "Contact support" (content-guide.md "Escalation CTA"): always visible in the customer chat. A deployment can
// point it at its own help desk; by default it opens the fictional TechCare contact page.
export const SUPPORT_HREF = process.env.NEXT_PUBLIC_SUPPORT_URL ?? "/support";
