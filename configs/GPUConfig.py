# Copyright (c) 2012 Mark D. Hill and David A. Wood
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met: redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer;
# redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution;
# neither the name of the copyright holders nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# Authors: Jason Power, Joel Hestness

import m5
import os
from m5.objects import *
from m5.util.convert import *

def addGPUOptions(parser):
    parser.add_option("--clusters", default=16, help="Number of shader core clusters in the gpu that GPGPU-sim is simulating", type="int")
    parser.add_option("--cores_per_cluster", default=1, help="Number of shader cores per cluster in the gpu that GPGPU-sim is simulating", type="int")
    parser.add_option("--ctas_per_shader", default=8, help="Number of simultaneous CTAs that can be scheduled to a single shader", type="int")
    parser.add_option("--sc_l1_size", default="64kB", help="size of l1 cache hooked up to each sc")
    parser.add_option("--sc_l2_size", default="1MB", help="size of L2 cache divided by num L2 caches")
    parser.add_option("--sc_l1_assoc", default=4, help="associativity of l1 cache hooked up to each sc", type="int")
    parser.add_option("--sc_l2_assoc", default=16, help="associativity of L2 cache backing SC L1's", type="int")
    parser.add_option("--shMemDelay", default=1, help="delay to access shared memory in gpgpu-sim ticks", type="int")
    parser.add_option("--kernel_stats", default=False, action="store_true", help="Dump statistics on GPU kernel boundaries")
    parser.add_option("--total-mem-size", default='2GB', help="Total size of memory in system")
    parser.add_option("--gpu_l1_buf_depth", type="int", default=1024, help="Number of buffered L1 requests per shader")
    parser.add_option("--gpu-core-clock", default='700MHz', help="The frequency of GPU clusters (note: shaders operate at double this frequency when modeling Fermi)")
    parser.add_option("--access-host-pagetable", action="store_true", default=False)
    parser.add_option("--split", default=False, action="store_true", help="Use split CPU and GPU cache hierarchies instead of fusion")
    parser.add_option("--dev-numa-high-bit", type="int", default=0, help="High order address bit to use for device NUMA mapping.")
    parser.add_option("--num-dev-dirs", default=1, help="In split hierarchies, number of device directories", type="int")
    parser.add_option("--gpu-mem-size", default='1GB', help="In split hierarchies, amount of GPU memory")
    parser.add_option("--gpu_mem_ctl_latency", type="int", default=-1, help="GPU memory controller latency in cycles")
    parser.add_option("--gpu_mem_freq", type="string", default=None, help="GPU memory controller frequency")
    parser.add_option("--gpu_membus_busy_cycles", type="int", default=-1, help="GPU memory bus busy cycles per data transfer")
    parser.add_option("--gpu_membank_busy_time", type="string", default=None, help="GPU memory bank busy time in ns (CL+tRP+tRCD+CAS)")
    parser.add_option("--gpu_warp_size", type="int", default=32, help="Number of threads per warp, also functional units per shader core/SM")

def configureMemorySpaces(options):
    total_mem_range = AddrRange(options.total_mem_size)
    cpu_mem_range = total_mem_range
    gpu_mem_range = total_mem_range

    if options.split:
        buildEnv['PROTOCOL'] +=  '_split'
        total_mem_size = total_mem_range.size()
        gpu_mem_range = AddrRange(options.gpu_mem_size)
        if gpu_mem_range.size() >= total_mem_size:
            print "GPU memory size (%s) won't fit within total memory size (%s)!" % (options.gpu_mem_size, options.total_mem_size)
            sys.exit(1)
        gpu_segment_base_addr = Addr(total_mem_size - gpu_mem_range.size())
        gpu_mem_range = AddrRange(gpu_segment_base_addr, size = options.gpu_mem_size)
        options.total_mem_size = long(gpu_segment_base_addr)
        cpu_mem_range = AddrRange(options.total_mem_size)
    else:
        buildEnv['PROTOCOL'] +=  '_fusion'
    return (cpu_mem_range, gpu_mem_range)

def parseGpgpusimConfig(options):
    # parse gpgpu config file
    # First check the cwd, and if there is not a gpgpusim.config file there
    # Use the template found in gem5-fusion/configs/gpu_config and fill in
    # the missing information with command line options.
    usingTemplate = False
    gpgpusimconfig = os.path.join(os.getcwd(), 'gpgpusim.config')
    if not os.path.isfile(gpgpusimconfig):
        gpgpusimconfig = os.path.join(os.path.dirname(__file__), 'gpu_config/gpgpusim.config.template')
        usingTemplate = True
        if not os.path.isfile(gpgpusimconfig):
            print >>sys.stderr, "Unable to find gpgpusim config (%s)" % gpgpusimconfig
            sys.exit(1)
    f = open(gpgpusimconfig, 'r')
    config = f.read()
    f.close()

    if usingTemplate:
        print "Using template and command line options for gpgpusim.config"
        config = config.replace("%clusters%", str(options.clusters))
        config = config.replace("%cores_per_cluster%", str(options.cores_per_cluster))
        config = config.replace("%ctas_per_shader%", str(options.ctas_per_shader))
        config = config.replace("%icnt_file%", os.path.join(os.path.dirname(__file__), "gpu_config/icnt_config_fermi_islip.txt"))
        config = config.replace("%warp_size%", str(options.gpu_warp_size))
        # GPGPU-Sim config expects freq in MHz
        config = config.replace("%freq%", str(toFrequency(options.gpu_core_clock) / 1.0e6))
        options.num_sc = options.clusters*options.cores_per_cluster
        f = open(m5.options.outdir + '/gpgpusim.config', 'w')
        f.write(config)
        f.close()
        gpgpusimconfig = m5.options.outdir + '/gpgpusim.config'
    else:
        print "Using gpgpusim.config for clusters, cores_per_cluster, Frequency, warp size"
        start = config.find("-gpgpu_n_clusters ") + len("-gpgpu_n_clusters ")
        end = config.find('-', start)
        gpgpu_n_clusters = int(config[start:end])
        start = config.find("-gpgpu_n_cores_per_cluster ") + len("-gpgpu_n_cores_per_cluster ")
        end = config.find('-', start)
        gpgpu_n_cores_per_cluster = int(config[start:end])
        num_sc = gpgpu_n_clusters * gpgpu_n_cores_per_cluster
        options.num_sc = num_sc
        start = config.find("-gpgpu_clock_domains ") + len("-gpgpu_clock_domains ")
        end = config.find(':', start)
        options.gpu_core_clock = config[start:end] + "MHz"
        start = config.find('-gpgpu_shader_core_pipeline ') + len('-gpgpu_shader_core_pipeline ')
        start = config.find(':', start) + 1
        end = config.find('\n', start)
        options.gpu_warp_size = int(config[start:end])

    return gpgpusimconfig

def createGPU(options, gpu_mem_range):
    gpgpusimOptions = parseGpgpusimConfig(options)

    gpu = StreamProcessorArray(manage_gpu_memory = options.split,
            gpu_memory_range = gpu_mem_range)

    gpu.shader_cores = [ShaderCore(id = i) for i in xrange(options.num_sc)]
    gpu.ce = SPACopyEngine(driver_delay = 5000000)

    gpu.frequency = options.gpu_core_clock
    gpu.warp_size = options.gpu_warp_size

    for sc in gpu.shader_cores:
        sc.lsq = ShaderLSQ()
        sc.lsq.warp_size = options.gpu_warp_size

    # This is a stop-gap solution until we implement a better way to register device memory
    if options.access_host_pagetable:
        for sc in gpu.shader_cores:
            sc.itb.access_host_pagetable = True
            sc.dtb.access_host_pagetable = True
            sc.lsq.data_tlb.access_host_pagetable = True
        gpu.ce.device_dtb.access_host_pagetable = True
        gpu.ce.host_dtb.access_host_pagetable = True

    gpu.shared_mem_delay = options.shMemDelay
    gpu.config_path = gpgpusimOptions
    gpu.dump_kernel_stats = options.kernel_stats

    return gpu

def connectGPUPorts(gpu, ruby, options):
    for i,sc in enumerate(gpu.shader_cores):
        sc.data_port = ruby._cpu_ruby_ports[options.num_cpus+i].slave
        sc.inst_port = ruby._cpu_ruby_ports[options.num_cpus+i].slave
        for j in xrange(options.gpu_warp_size):
            sc.lsq_port[j] = sc.lsq.lane_port[j]
        sc.lsq.cache_port = ruby._cpu_ruby_ports[options.num_cpus+i].slave

    gpu.ce.host_port = ruby._cpu_ruby_ports[options.num_cpus+options.num_sc].slave
    if options.split:
        gpu.ce.device_port = ruby._cpu_ruby_ports[options.num_cpus+options.num_sc+1].slave
    else:
        # With a unified address space, tie both copy engine ports to the same
        # copy engine controller
        gpu.ce.device_port = ruby._cpu_ruby_ports[options.num_cpus+options.num_sc].slave