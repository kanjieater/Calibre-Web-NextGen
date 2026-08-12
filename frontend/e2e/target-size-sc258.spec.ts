import { expect, test, type Locator } from '@playwright/test';

type HitExtent = {
  visual: [number, number];
  effective: [number, number];
};

async function measureEffectiveHitExtent(control: Locator): Promise<HitExtent> {
  // elementFromPoint() uses viewport coordinates. Keeping the control in view
  // before probing is therefore part of the measurement, not test setup fluff.
  await control.scrollIntoViewIfNeeded();

  return control.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const centreX = rect.left + rect.width / 2;
    const centreY = rect.top + rect.height / 2;
    const ownsHit = (hit: Element | null) =>
      hit !== null && (hit === element || element.contains(hit));

    const findBoundary = (
      visualRadius: number,
      hitAtDistance: (distance: number) => boolean,
    ) => {
      const step = 0.25;
      let lastHit = 0;
      let firstMiss = visualRadius;

      // Walk from the visual centre through the visual edge and outward. The
      // first non-owned point captures clipping and paint-order interception.
      for (let distance = step; distance <= visualRadius + 40; distance += step) {
        if (!hitAtDistance(distance)) {
          firstMiss = distance;
          break;
        }
        lastHit = distance;
      }

      // Refine the hit/miss boundary so integer pixel-centre quantisation does
      // not turn a genuine 24px target into a reported 23px target.
      for (let iteration = 0; iteration < 12; iteration += 1) {
        const candidate = (lastHit + firstMiss) / 2;
        if (hitAtDistance(candidate)) lastHit = candidate;
        else firstMiss = candidate;
      }
      return (lastHit + firstMiss) / 2;
    };

    const top = findBoundary(rect.height / 2, (distance) =>
      ownsHit(document.elementFromPoint(centreX, centreY - distance)));
    const bottom = findBoundary(rect.height / 2, (distance) =>
      ownsHit(document.elementFromPoint(centreX, centreY + distance)));
    const left = findBoundary(rect.width / 2, (distance) =>
      ownsHit(document.elementFromPoint(centreX - distance, centreY)));
    const right = findBoundary(rect.width / 2, (distance) =>
      ownsHit(document.elementFromPoint(centreX + distance, centreY)));

    return {
      visual: [Number(rect.width.toFixed(0)), Number(rect.height.toFixed(0))],
      effective: [
        // Chromium resolves hit-test coordinates to device-pixel cells. The
        // two boundary searches therefore include one shared pixel cell;
        // remove it to report the CSS-pixel distance between the boundaries.
        Number((left + right - 1).toFixed(0)),
        Number((top + bottom - 1).toFixed(0)),
      ],
    };
  });
}

async function expectSc258Target(label: string, control: Locator) {
  await expect(control).toBeVisible();
  const measured = await measureEffectiveHitExtent(control);
  console.log(
    `${label}: visual ${measured.visual[0]}x${measured.visual[1]}, `
      + `effective ${measured.effective[0]}x${measured.effective[1]}`,
  );
  expect.soft(measured.effective[0], `${label} effective clickable width`).toBeGreaterThanOrEqual(24);
  expect.soft(measured.effective[1], `${label} effective clickable height`).toBeGreaterThanOrEqual(24);
}

test('compact controls expose at least a 24x24 effective clickable target', async ({ page }) => {
  // Keep the desktop browser context (fine pointer) while exercising the
  // responsive menu. Mobile emulation would activate the unrelated 44px
  // coarse-pointer rule and make the pre-fix control pass.
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto('/app/');
  await expectSc258Target('TopBar menu button', page.getByRole('button', { name: 'Open navigation' }));

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/app/account');
  await expectSc258Target('Account revoke button', page.locator('[class*="revokeBtn"]').first());

  await page.goto('/app/book/191/edit');
  await expectSc258Target('EditBook format delete button', page.locator('[class*="formatDelete"]').first());
});
