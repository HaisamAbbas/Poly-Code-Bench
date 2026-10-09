import type { Page } from "@playwright/test";

export async function inspectPage(page: Page) {
  return page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const context = canvas.getContext("2d")!;
    function rgba(color: string): number[] {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      return Array.from(context.getImageData(0, 0, 1, 1).data);
    }
    function luminance(rgb: number[]): number {
      const values = rgb.slice(0, 3).map((channel) => {
        const value = channel / 255;
        return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
      });
      return .2126 * values[0] + .7152 * values[1] + .0722 * values[2];
    }
    const contrastFailures: string[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const element = node.parentElement;
      if (!node.textContent?.trim() || !element || element.closest("script, style, svg, option, .sr-only, .skip-link, nextjs-portal")) continue;
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      if (!rect.width || !rect.height || style.visibility === "hidden" || style.display === "none") continue;
      const parents: Element[] = [];
      for (let parent: Element | null = element; parent; parent = parent.parentElement) parents.unshift(parent);
      let background = [255, 255, 255];
      for (const parent of parents) {
        const layer = rgba(getComputedStyle(parent).backgroundColor);
        const alpha = layer[3] / 255;
        background = background.map((channel, index) => channel * (1 - alpha) + layer[index] * alpha);
      }
      const foreground = rgba(style.color);
      const a = luminance(foreground);
      const b = luminance(background);
      const contrast = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
      const large = parseFloat(style.fontSize) >= 24 || (parseFloat(style.fontSize) >= 18.66 && Number(style.fontWeight) >= 700);
      if (contrast + .03 < (large ? 3 : 4.5)) contrastFailures.push(`${element.className || element.tagName}: ${contrast.toFixed(2)} ${node.textContent.trim().slice(0, 60)}`);
    }
    const targetFailures = Array.from(document.querySelectorAll("button, select, input:not([type=hidden]):not([type=checkbox]), summary, .brand, .primary-nav a, .data-table a, .task-row-title a, .language-index a"))
      .filter((element) => element.getClientRects().length && getComputedStyle(element).visibility !== "hidden")
      .filter((element) => element.getBoundingClientRect().height < 39.5)
      .map((element) => `${element.tagName} ${element.textContent?.trim().slice(0, 40)}`);
    return {
      viewport: innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      contrastFailures: [...new Set(contrastFailures)],
      targetFailures,
      tables: Array.from(document.querySelectorAll<HTMLDivElement>(".table-wrap")).map((region) => ({
        label: region.getAttribute("aria-label"),
        layout: region.dataset.layout,
        clientWidth: region.clientWidth,
        contentWidth: region.scrollWidth,
        rows: region.querySelectorAll("tbody tr").length,
        labelledCells: region.querySelectorAll("tbody [data-label]").length,
      })),
    };
  });
}
