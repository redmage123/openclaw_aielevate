import { create } from "zustand";
import { createControllerProxy } from "./controller-proxy";
import { loadChannels as loadChannelsCtrl } from "../../ui/controllers/channels";

type ChannelsState = {
  channelsLoading: boolean;
  channelsSnapshot: unknown;
  channelsError: string | null;
  channelsLastSuccess: number | null;
  whatsappLoginMessage: string | null;
  whatsappLoginQrDataUrl: string | null;
  whatsappLoginConnected: boolean | null;
  whatsappBusy: boolean;
};

type ChannelsActions = {
  load: (probe?: boolean) => Promise<void>;
};

export const useChannelsStore = create<ChannelsState & ChannelsActions>(() => ({
  channelsLoading: false,
  channelsSnapshot: null,
  channelsError: null,
  channelsLastSuccess: null,
  whatsappLoginMessage: null,
  whatsappLoginQrDataUrl: null,
  whatsappLoginConnected: null,
  whatsappBusy: false,

  load: async (probe = false) => {
    const proxy = createControllerProxy();
    await loadChannelsCtrl(proxy as never, probe);
  },
}));

export default useChannelsStore;
