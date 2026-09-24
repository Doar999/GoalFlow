import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "@/app/App";
import { AppProviders } from "@/app/providers";
import "@/app/styles.css";

const container = document.getElementById("root");
if (container === null) {
  throw new Error("找不到 #root 挂载点，请检查 index.html");
}

createRoot(container).render(
  <StrictMode>
    <AppProviders>
      <App />
    </AppProviders>
  </StrictMode>,
);
