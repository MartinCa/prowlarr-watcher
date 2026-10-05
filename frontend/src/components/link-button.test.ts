import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { LinkButton } from "@/components/link-button";

function render(
  props: Record<string, unknown>,
  linkProps: Record<string, unknown>,
  children?: string,
) {
  return renderToStaticMarkup(
    createElement(LinkButton, { ...props, render: createElement("a", linkProps) }, children),
  );
}

describe("LinkButton", () => {
  it("renders a plain anchor with the href, slot marker and no role", () => {
    const html = render({}, { href: "/queries" }, "Queries");

    expect(html).toMatch(/^<a /);
    expect(html).toContain('href="/queries"');
    expect(html).toContain('data-slot="button"');
    expect(html).toContain(">Queries</a>");
    expect(html).not.toContain("role=");
  });

  it("applies the variant and size classes", () => {
    const outline = render({ variant: "outline", size: "sm" }, { href: "/x" }, "x");
    const plain = render({}, { href: "/x" }, "x");

    expect(outline).toContain("border-border");
    expect(plain).not.toContain("border-border");
    expect(outline).not.toBe(plain);
  });

  it("merges the consumer and rendered-element classNames instead of clobbering them", () => {
    const html = render(
      { className: "consumer-marker" },
      { href: "/x", className: "element-marker" },
      "x",
    );

    expect(html).toContain("consumer-marker");
    expect(html).toContain("element-marker");
    expect(html).toContain("inline-flex");
  });

  it("lets explicit children override the rendered element's own children", () => {
    const html = renderToStaticMarkup(
      createElement(
        LinkButton,
        { render: createElement("a", { href: "/x" }, "element child") },
        "explicit child",
      ),
    );

    expect(html).toContain("explicit child");
    expect(html).not.toContain("element child");
  });

  it("keeps the rendered element's children when LinkButton has none", () => {
    const html = renderToStaticMarkup(
      createElement(LinkButton, { render: createElement("a", { href: "/x" }, "element child") }),
    );

    expect(html).toContain("element child");
  });
});
