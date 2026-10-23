import { JARVIS_NO_CLOUD_URL } from '@proj-airi/stage-shared/jarvis'

// JARVIS: no AIRI account server.
export const SERVER_URL = import.meta.env.VITE_SERVER_URL || JARVIS_NO_CLOUD_URL
