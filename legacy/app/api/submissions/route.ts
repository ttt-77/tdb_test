import { NextResponse } from "next/server";
import { isAdminAuthorized, listSubmissions } from "@/lib/storage";

export async function GET(req: Request) {
  if (!isAdminAuthorized(req)) {
    return NextResponse.json({ ok: false, error: "unauthorized" }, { status: 401 });
  }
  try {
    const items = await listSubmissions();
    items.sort((a, b) => (a.submittedAt < b.submittedAt ? 1 : -1));
    return NextResponse.json({ ok: true, items });
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: e?.message || "List failed" },
      { status: 500 },
    );
  }
}
