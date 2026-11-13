// OunceMCP report renderer: the A2UI v0.9 Lit renderer and basic catalog
// (https://a2ui.org), bundled into one file so reports open offline.
// Rebuild with ./build.sh; the output is src/ouncemcp/ui/static/a2ui-render.js.
import {MessageProcessor} from '@a2ui/web_core/v0_9';
import {A2uiSurface, basicCatalog} from '@a2ui/lit/v0_9';

// Renders A2UI messages into `container`. `onAction(action, processor)` receives
// button events; it may answer with more messages through `processor`.
export function render(messages, container, onAction) {
  let processor;
  processor = new MessageProcessor([basicCatalog], action => {
    if (onAction) onAction(action, processor);
  });
  processor.onSurfaceCreated(surface => {
    const el = document.createElement('a2ui-surface');
    el.surface = surface;
    container.appendChild(el);
  });
  processor.processMessages(messages);
  return processor;
}
void A2uiSurface; // registers <a2ui-surface>
window.OunceA2UI = {render};
