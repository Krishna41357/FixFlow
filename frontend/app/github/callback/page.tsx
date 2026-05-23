"use client";
import { useEffect } from "react";
import { useSearchParams } from "next/navigation";

export default function GitHubCallbackPage() {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const accessToken = params.get("access_token");
    const connId = params.get("connection_id");

    // Pass data to parent window then close
    if (window.opener) {
      window.opener.postMessage(
        {
          type: "github_oauth_success",
          access_token: accessToken,
          connection_id: connId,
          github_login: params.get("github_login"),
          installations: params.get("installations"),
        },
        window.location.origin
      );
      window.close();
    }
  }, []);

  return (
    <div className="min-h-screen bg-[#0b0c0f] flex items-center justify-center">
      <p className="text-slate-400 text-sm">Completing authorization, closing...</p>
    </div>
  );
}