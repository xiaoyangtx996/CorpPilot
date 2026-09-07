// The workspace groups secondary operations in the native management disclosure.
export async function openManagement(page) {
  const tools = page.locator('details.management-tools');
  if (await tools.getAttribute('open') === null) await tools.locator('summary').click();
}
