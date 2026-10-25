/// <reference lib="webworker" />
/** The vault graph's simulation off the page's thread (protocol: `simHost` in graphSim.ts). */
import { simHost, type ToSim } from './graphSim';

const scope = self as unknown as DedicatedWorkerGlobalScope;
const handle = simHost((message, transfer) => scope.postMessage(message, transfer));
scope.onmessage = (event: MessageEvent<ToSim>) => handle(event.data);
