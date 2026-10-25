import { redirect } from "next/navigation";

// The product's front door is the customer chat (scope.md §4). The local stack check lives at /status.
export default function Home() {
  redirect("/chat");
}
