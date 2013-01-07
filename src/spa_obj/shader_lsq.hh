/*
 * Copyright (c) 2012 Mark D. Hill and David A. Wood
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are
 * met: redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer;
 * redistributions in binary form must reproduce the above copyright
 * notice, this list of conditions and the following disclaimer in the
 * documentation and/or other materials provided with the distribution;
 * neither the name of the copyright holders nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
 * OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#ifndef __GPGPU_SHADER_LSQ_HH__
#define __GPGPU_SHADER_LSQ_HH__

#include <deque>
#include <list>
#include <vector>

#include "cpu/translation.hh"
#include "mem/mem_object.hh"
#include "mem/packet_queue.hh"
#include "mem/port.hh"
#include "mem/qport.hh"
#include "params/ShaderLSQ.hh"

class ShaderLSQ : public MemObject
{
protected:
    typedef ShaderLSQParams Params;

private:

    /**
     * Port which receives requests from the shader core on a per-lane basis
     * and sends replies to the shader core.
     */
    class LanePort : public SlavePort
    {
    private:
        int laneId;

    public:
        LanePort(const std::string &_name, int idx, ShaderLSQ *owner)
        : SlavePort(_name, owner), laneId(idx), isBlocked(false) {}

        // Just for sanity
        bool isBlocked;

    protected:
        virtual bool recvTimingReq(PacketPtr pkt);
        virtual Tick recvAtomic(PacketPtr pkt);
        virtual void recvFunctional(PacketPtr pkt);
        virtual void recvRetry();
        virtual AddrRangeList getAddrRanges() const;

    };
    /// One lane port for each lane in the Shader Core
    std::vector<LanePort*> lanePorts;

    // For tracking if the port to send to the shader core is blocked
    bool responsePortBlocked;

    /**
     * Port which sends the coalesced requests to the ruby port
     */
    class CachePort : public MasterPort
    {
    private:

    public:
        CachePort(const std::string &_name, ShaderLSQ *owner)
        : MasterPort(_name, owner) {}
    protected:
        virtual bool recvTimingResp(PacketPtr pkt);
        virtual void recvRetry();
    };
    CachePort cachePort;

    // Number of lanes, and number of threads active in a cycle
    int warpSize;

    // Should remove this when we are confident in the coalescing logic
    // From GPGPU-Sim
    class transaction_info {
    public:
        std::bitset<4> chunks; // bitmask: 32-byte chunks accessed
        // mem_access_byte_mask_t bytes;
        std::vector<int> activeLanes; // threads in this transaction

        // bool test_bytes(unsigned start_bit, unsigned end_bit) {
        //    for( unsigned i=start_bit; i<=end_bit; i++ )
        //       if(bytes.test(i))
        //          return true;
        //    return false;
        // }

    };

    // Forward definition for WarpRequest
    class CoalescedRequest;

    /**
     * There is exactly one warp request per ld/st inst issued by the shader
     * This object holds the information needed to respond to the shader core
     * on a per-lane basis. It also has functions to get and set the data/addrs
     * There is one warp request for possibly many coalesced requests
     */
    class WarpRequest {
    private:
        std::vector<bool> activeMask;
        int warpSize;

    public:
        std::list<CoalescedRequest*> coalescedRequests;
        std::vector<PacketPtr> laneRequests;
        Tick occupiedTick;
        Cycles occupiedCycle;
        int size; // size in bytes of each lane request
        bool read;
        bool write;
        Addr pc;
        int cid;
        int warpId;
        MasterID masterId;

        WarpRequest(int _warpSize)
            : activeMask(_warpSize), warpSize(_warpSize),
              laneRequests(warpSize), size(0), read(false), write(false) {}

        bool isValid(int laneId) {
            assert(laneId < warpSize);
            return activeMask[laneId];
        }

        void setValid(int laneId) {
            assert(laneId < warpSize);
            activeMask[laneId] = true;
        }

        Addr getAddr(int laneId) {
            assert(laneId < warpSize);
            return laneRequests[laneId]->req->getVaddr();
        }

        uint8_t* getData(int laneId) {
            assert(laneId < warpSize);
            return laneRequests[laneId]->getPtr<uint8_t>();
        }

        void setData(int laneId, uint8_t *data) {
            assert(laneId < warpSize);
            return laneRequests[laneId]->setData(data);
        }
    };
    // The current lane request being processed by the coalescer
    // This could be a finite buffer at some point
    WarpRequest *coalescingRegister;

    /**
     * There is exactly on coalesced request per request sent to Ruby/the cache
     * This object holds the information needed to make a packet to send to
     * ruby. This object also incapulates the information the load/store queues
     * need to keep for each request.
     * There are (possibly) multiple coalesced requests per warp request.
     */
    class CoalescedRequest : public Packet::SenderState {
    public:
        RequestPtr req;
        WarpRequest* warpRequest;
        std::vector<int> activeLanes;
        int wordSize;
        bool read;
        bool write;
        int bufferNum;
        uint8_t *data;

        bool done;
        bool sent;
        bool translated;
        bool valid;
        ~CoalescedRequest() { delete [] data; }
    };

    // Logically equivielnt to NVIDIA's ld/st units
    std::vector<std::vector<CoalescedRequest*> > requestBuffers;
    int requestBufferDepth;

    // Which of the request buffers are blocked
    std::list<int> blockedBufferNums;

    // Temporary buffer for coalesced requests while the warp request is
    // being coalesced
    std::list<CoalescedRequest*> coalescedRequests;

    // Queue of warp requests which have completely finished but have not been
    // sent to the ShaderCore yet. This queue is currently infinite
    std::deque<WarpRequest*> responseQueue;

    /**
     * This event is scheduled when a coalesced request in a requestBuffer
     * is ready to be sent to Ruby/the cache.
     */
    class SendRubyRequestEvent : public Event
    {
    private:
        ShaderLSQ *owner;
        int requestBufferNum;
    public:
        SendRubyRequestEvent(ShaderLSQ *_owner, int _requestBufferNum)
            : owner(_owner), requestBufferNum(_requestBufferNum) {}
        void process() {
            owner->sendRubyRequest(requestBufferNum);
        }
    };
    // One event per request buffer
    std::vector<SendRubyRequestEvent*> sendRubyRequestEvents;

    // Data TLB, this does NOT perform any timing right now
    ShaderTLB *tlb;

    /**
     * Returns the request buffer that this coalesced request would go to
     * Currently hashed on low bits above line offset. Theoretically this is
     * the same banking as in the L1 cache.
     */
    std::vector<CoalescedRequest*>& getRequestBuffer(CoalescedRequest *request);

    /**
     * These functions will insert/remvoe a coalesced request from its buffer.
     * Internally uses getRequestBuffer to locate the buffer for the request
     * @return true for success, false if buffer is full
     * (remove cannot fail)
     */
    bool insertRequestIntoBuffer(CoalescedRequest *request);
    void removeRequestFromBuffer(CoalescedRequest *request);

    /**
     * Sets up the translation and sends it to the TLB
     */
    void beginTranslation(CoalescedRequest *request);

    /**
     * Coalesces the WarpRequest in this->warpRequest and inserts the generated
     * coalesced requests into this->coalescedRequests. Later these are added
     * to the request buffers.
     */
    void coalesce(WarpRequest *warpRequest);

    /**
     * Actually generate the coalesced request, called from coalesce()
     */
    void generateMemoryAccess(Addr addr, size_t size, WarpRequest *warpRequest,
                              std::vector<int> &activeLanes);


public:

    ShaderLSQ(Params *params);
    ~ShaderLSQ();

    /// Required for implementing MemObject
    virtual BaseMasterPort& getMasterPort(const std::string &if_name, PortID idx = -1);
    virtual BaseSlavePort& getSlavePort(const std::string &if_name, PortID idx = -1);

    /**
     * Sends ALL requests in the buffer number provided to ruby
     */
    void sendRubyRequest(int requestBufferNum);

    /**
     * For each buffer that has been blocked, schedule sendRubyRequest
     * Called when Ruby sends a retry. This does more work than it needs to
     * but Ruby doesn't know what buffer blocked.
     */
    void rescheduleBlockedRequestBuffers();

    /**
     * Prepares the per-lane packets for sending and puts them the repsonse Q
     * Pkt is for the coalesced request so this function must 'uncoalesce'
     */
    bool prepareResponse(PacketPtr pkt);

    /**
     * Required for translation
     */
    bool isSquashed() { return false; }

    /**
     * Called when translation is finished
     */
    void finishTranslation(WholeTranslationState *state);

    /**
     * Coalesces the warp request and subsequently empties the
     * coalesced requests into the request buffers possibly over many cycles
     */
    void processCoalesceEvent();
    EventWrapper<ShaderLSQ, &ShaderLSQ::processCoalesceEvent> coalesceEvent;

    /**
     * Sends one response to the ShaderCore from the response queue
     */
    void processSendResponseEvent();
    EventWrapper<ShaderLSQ, &ShaderLSQ::processSendResponseEvent> sendResponseEvent;

};

#endif