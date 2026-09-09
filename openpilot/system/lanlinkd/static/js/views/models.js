import { setCleanup } from "../router.js";
export async function renderModels(app) { setCleanup(() => {}); app.innerHTML = "<div class='empty-state'>加载中…</div>"; }
