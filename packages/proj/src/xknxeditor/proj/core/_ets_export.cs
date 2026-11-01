using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Linq.Expressions;
using System.Reflection;
using System.Runtime.ExceptionServices;
using System.Security;
using System.Security.Permissions;
using System.Security.Policy;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Threading;
using System.Xml;
using Expression = System.Linq.Expressions.Expression;

namespace EtsSignExport
{
  internal static class Program
  {
    private static readonly string[] RequiredFiles =
    {
      "ETS6N.exe", "ETS6N.exe.config", "Autofac.dll", "Knx.Ets.Sdk.dll",
      "Knx.Ets.ObjectModel.dll", "Knx.Ets.Xml.ObjectModel.dll", "Knx.Ets.ViewModel.dll",
      "Knx.Ets.HawkNET.dll", "Knx.Ets.ObjectModelWindowsBase.dll",
    };

    [STAThread]
    private static int Main(string[] args)
    {
      var stdout = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true };
      Console.SetOut(stdout);
      int code;
      try
      {
        code = Run(args);
      }
      catch (Exception ex)
      {
        Error(Text.OneLine(ex.Message));
        code = 1;
      }
      Console.Out.Flush();
      Environment.Exit(code);
      return code;
    }

    private static void Error(string message)
    {
      Console.Out.WriteLine("ERROR=" + message);
    }

    private static int Run(string[] args)
    {
      string etsDir, input, output, password;
      bool acceptUntested;
      string parseError;
      if (!ParseArgs(args, out etsDir, out input, out output, out password, out acceptUntested, out parseError))
      {
        Error(parseError);
        return 2;
      }

      if (Environment.OSVersion.Platform != PlatformID.Win32NT)
      {
        Error("this tool runs only on Windows with a locally installed, licensed ETS 6");
        return 3;
      }

      etsDir = Path.GetFullPath(etsDir).TrimEnd(Path.DirectorySeparatorChar);
      if (!Directory.Exists(etsDir))
      {
        Error("the ETS directory '" + etsDir + "' does not exist");
        return 3;
      }
      foreach (var file in RequiredFiles)
      {
        if (!File.Exists(Path.Combine(etsDir, file)))
        {
          Error("the ETS directory is missing '" + file + "'");
          return 3;
        }
      }

      string versionError;
      if (!VersionAllowed(Path.Combine(etsDir, "ETS6N.exe"), acceptUntested, out versionError))
      {
        Error(versionError);
        return 3;
      }

      string inputFull, outputFull;
      try
      {
        inputFull = Path.GetFullPath(input);
        outputFull = Path.GetFullPath(output);
      }
      catch (Exception ex)
      {
        Error("a path is not valid: " + Text.OneLine(ex.Message));
        return 2;
      }

      if (!File.Exists(inputFull))
      {
        Error("the input file '" + inputFull + "' does not exist");
        return 2;
      }
      if (string.Equals(inputFull, outputFull, StringComparison.OrdinalIgnoreCase))
      {
        Error("the output file must differ from the input file; the input is never modified");
        return 2;
      }
      var outputParent = Path.GetDirectoryName(outputFull);
      if (string.IsNullOrEmpty(outputParent) || !Directory.Exists(outputParent))
      {
        Error("the output folder does not exist");
        return 2;
      }

      ArchiveShape shape;
      try
      {
        shape = ArchiveShape.Inspect(inputFull);
      }
      catch (Exception)
      {
        Error("'" + inputFull + "' cannot be read as a project archive (.knxproj is a ZIP file)");
        return 2;
      }
      if (shape.ProjectCount != 1)
      {
        Error("the file contains " + shape.ProjectCount + " project(s); exactly one is required");
        return 2;
      }
      if (shape.IsProtected && string.IsNullOrEmpty(password))
      {
        Error("the project is password protected; pass --password");
        return 5;
      }

      var inputHashBefore = Digest.OfFile(inputFull);
      var fingerprintBefore = StoreFingerprint.Take();

      var workDir = Path.Combine(Path.GetTempPath(), "ets-sign-" + Guid.NewGuid().ToString("N"));
      Directory.CreateDirectory(workDir);
      var tempDir = Path.Combine(workDir, "Temp");
      Directory.CreateDirectory(tempDir);

      try
      {
      var previousTmp = Environment.GetEnvironmentVariable("TMP");
      var previousTemp = Environment.GetEnvironmentVariable("TEMP");
      Environment.SetEnvironmentVariable("TMP", tempDir);
      Environment.SetEnvironmentVariable("TEMP", tempDir);

      var request = new ExportRequest
      {
        EtsDirectory = etsDir,
        InputPath = inputFull,
        WorkDirectory = workDir,
        Password = password,
      };

      ExportResult result;
      var setup = new AppDomainSetup
      {
        ApplicationBase = etsDir,
        ConfigurationFile = Path.Combine(etsDir, "ETS6N.exe.config"),
        DisallowCodeDownload = true,
      };
      var domain = AppDomain.CreateDomain("ets-sign", null, setup,
        new PermissionSet(PermissionState.Unrestricted), new StrongName[0]);
      try
      {
        var worker = (ExportWorker)domain.CreateInstanceFromAndUnwrap(
          typeof(ExportWorker).Assembly.Location, typeof(ExportWorker).FullName);
        result = worker.Run(request, new Progress());
      }
      finally
      {
        Environment.SetEnvironmentVariable("TMP", previousTmp);
        Environment.SetEnvironmentVariable("TEMP", previousTemp);
        try { AppDomain.Unload(domain); }
        catch (CannotUnloadAppDomainException) { }
      }

      if (!result.Success)
      {
        Error(Text.OneLine(result.Message) ?? "the export failed");
        return result.ExitCode != 0 ? result.ExitCode : 1;
      }

      string publishError;
      var notes = new List<string>(result.Notes ?? new List<string>());
      if (!Publish(workDir, outputFull, inputFull, inputHashBefore, result, password, out publishError))
      {
        Error(Text.OneLine(publishError));
        return 1;
      }

      var fingerprintAfter = StoreFingerprint.Take();
      if (!StoreFingerprint.Same(fingerprintBefore, fingerprintAfter))
      {
        notes.Add("ETS data changed during the run; is ETS running? This tool writes nothing to the global ETS stores.");
      }

      Console.Out.WriteLine("LICENSE_NAME=" + Text.OneLine(result.LicenseName));
      Console.Out.WriteLine("LICENSE_NUMBER=" + Text.OneLine(result.LicenseNumber));
      Console.Out.WriteLine("LICENSED_DEVICES=" + result.LicensedDevices.ToString(CultureInfo.InvariantCulture));
      Console.Out.WriteLine("CERT=" + (result.Certificate ? "true" : "false"));
      Console.Out.WriteLine("VALIDATION=" + (result.Validation ? "true" : "false"));
      foreach (var note in notes)
      {
        var line = Text.OneLine(note);
        if (!string.IsNullOrEmpty(line))
        {
          Console.Out.WriteLine("NOTE=" + line);
        }
      }
      Console.Out.WriteLine("STATUS=ok");
      return 0;
      }
      finally
      {
        TryDeleteDirectory(workDir);
      }
    }

    private static bool ParseArgs(string[] args, out string etsDir, out string input,
      out string output, out string password, out bool acceptUntested, out string error)
    {
      etsDir = input = output = password = null;
      acceptUntested = false;
      error = null;
      var positional = new List<string>();
      for (var i = 0; i < args.Length; i++)
      {
        var arg = args[i];
        if (arg == "--password")
        {
          if (i + 1 >= args.Length) { error = "--password needs a value"; return false; }
          password = args[++i];
        }
        else if (arg == "--accept-untested")
        {
          acceptUntested = true;
        }
        else if (arg.StartsWith("--", StringComparison.Ordinal))
        {
          error = "unknown argument '" + arg + "'";
          return false;
        }
        else
        {
          positional.Add(arg);
        }
      }
      if (positional.Count != 3)
      {
        error = "usage: <exe> <ets-dir> <input.knxproj> <output.knxproj> [--password <pw>] [--accept-untested]";
        return false;
      }
      etsDir = positional[0];
      input = positional[1];
      output = positional[2];
      return true;
    }

    private static bool VersionAllowed(string executable, bool acceptUntested, out string error)
    {
      error = null;
      var raw = FileVersionInfo.GetVersionInfo(executable).FileVersion ?? "";
      var token = raw.Split(' ')[0];
      Version version;
      if (!Version.TryParse(token, out version))
      {
        error = "the ETS version could not be read from ETS6N.exe";
        return false;
      }
      if (version.Major != 6)
      {
        error = "ETS " + token + " is not supported; this tool works with ETS 6 only";
        return false;
      }
      if (version.Minor == 4)
      {
        return true;
      }
      if (!acceptUntested)
      {
        error = "ETS " + token + " was not tested with this tool; pass --accept-untested to continue";
        return false;
      }
      return true;
    }

    private static bool Publish(string workDir, string target, string inputFull,
      string inputHashBefore, ExportResult result, string password, out string error)
    {
      error = null;
      var source = Path.Combine(workDir, "out", "export.knxproj");
      if (!File.Exists(source))
      {
        error = "ETS produced no export file";
        return false;
      }
      if (!File.Exists(inputFull) || !string.Equals(Digest.OfFile(inputFull), inputHashBefore, StringComparison.Ordinal))
      {
        error = "the input file changed during the run; nothing was written";
        return false;
      }

      var content = File.ReadAllBytes(source);
      if (result.OutputSha256 != null && !string.Equals(Digest.OfBytes(content), result.OutputSha256, StringComparison.Ordinal))
      {
        error = "the copy of ETS's export differs from what ETS wrote";
        return false;
      }

      var secrets = new SecretScanner(new List<string> { password });
      if (result.Secrets != null)
      {
        secrets.AddRange(result.Secrets);
      }
      if (secrets.ContainsSecret(content))
      {
        error = "the export would contain the project password or a derived key; it was not written";
        return false;
      }

      var staging = target + ".tmp-" + Guid.NewGuid().ToString("N").Substring(0, 8);
      try
      {
        File.Copy(source, staging, false);
        // Replace (not delete-then-move): a failure here must not leave the previous export gone.
        if (File.Exists(target))
        {
          File.Replace(staging, target, null);
        }
        else
        {
          File.Move(staging, target);
        }
      }
      catch (Exception ex)
      {
        error = "the export could not be written: " + Text.OneLine(ex.Message);
        return false;
      }
      finally
      {
        try { if (File.Exists(staging)) File.Delete(staging); }
        catch (Exception) { }
      }
      if (result.Secrets != null)
      {
        Array.Clear(result.Secrets, 0, result.Secrets.Length);
      }
      return true;
    }

    private static void TryDeleteDirectory(string directory)
    {
      for (var attempt = 0; attempt < 6; attempt++)
      {
        try
        {
          if (Directory.Exists(directory))
          {
            Directory.Delete(directory, true);
          }
          return;
        }
        catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
        {
          Thread.Sleep(400);
        }
      }
    }
  }

  internal static class Text
  {
    public static string OneLine(string value)
    {
      if (value == null)
      {
        return null;
      }
      var builder = new StringBuilder(value.Length);
      foreach (var c in value)
      {
        builder.Append(c == '\r' || c == '\n' || c == '\t' ? ' ' : c);
      }
      return builder.ToString().Trim();
    }
  }

  internal sealed class Progress : MarshalByRefObject
  {
    public override object InitializeLifetimeService()
    {
      return null;
    }

    public void Report(string stage)
    {
      Console.Out.WriteLine("STAGE=" + stage);
      Console.Out.Flush();
    }
  }

  [Serializable]
  internal sealed class ExportRequest
  {
    public string EtsDirectory;
    public string InputPath;
    public string WorkDirectory;
    public string Password;
  }

  [Serializable]
  internal sealed class ExportResult
  {
    public bool Success;
    public string Message;
    public int ExitCode;
    public string LicenseName = "";
    public string LicenseNumber = "";
    public int LicensedDevices;
    public bool Certificate;
    public bool Validation;
    public int Devices;
    public int InputDevices = -1;
    public int GroupAddresses;
    public int InputGroupAddresses = -1;
    public bool OutputProtected;
    public string OutputSha256;
    public string[] Secrets;
    public List<string> Notes = new List<string>();
  }

  internal sealed class ExportWorker : MarshalByRefObject
  {
    public ExportWorker()
    {
      var self = typeof(ExportWorker).Assembly;
      var ownName = self.GetName().Name;
      var toolDirectory = Path.GetDirectoryName(self.Location);
      AppDomain.CurrentDomain.AssemblyResolve += (sender, resolveArgs) =>
      {
        var requested = new AssemblyName(resolveArgs.Name).Name ?? "";
        if (requested != ownName)
        {
          return null;
        }
        foreach (var extension in new[] { ".exe", ".dll" })
        {
          var candidate = Path.Combine(toolDirectory, ownName + extension);
          if (File.Exists(candidate))
          {
            return Assembly.UnsafeLoadFrom(candidate);
          }
        }
        return null;
      };
    }

    public override object InitializeLifetimeService()
    {
      return null;
    }

    public ExportResult Run(ExportRequest request, Progress progress)
    {
      var result = new ExportResult();
      var secrets = new SecretScanner(new List<string> { request.Password });
      Exception hostFailure = null;
      var completed = 0;

      var thread = new Thread(() =>
      {
        var dispatcher = Dispatcher.CurrentDispatcher;
        try
        {
          var application = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
          dispatcher.BeginInvoke((Action)(() =>
          {
            ExportSession session = null;
            try
            {
              session = new ExportSession(request, result, dispatcher, application, progress, secrets);
              if (!session.RunStartupPhase())
              {
                Close(session, dispatcher, ref completed, result);
              }
              else
              {
                var running = session;
                Task.Run((Action)running.RunWorkPhase).ContinueWith(task =>
                {
                  if (task.Exception != null)
                  {
                    RecordHostFailure(result, task.Exception.GetBaseException(), secrets);
                  }
                  dispatcher.BeginInvoke((Action)(() => Close(running, dispatcher, ref completed, result)));
                }, TaskScheduler.Default);
              }
            }
            catch (Exception ex)
            {
              RecordHostFailure(result, ex, secrets);
              if (session != null)
              {
                Close(session, dispatcher, ref completed, result);
              }
              else
              {
                dispatcher.BeginInvokeShutdown(DispatcherPriority.Background);
              }
            }
          }));
          Dispatcher.Run();
        }
        catch (Exception ex)
        {
          if (Volatile.Read(ref completed) == 1)
          {
            lock (result.Notes)
            {
              result.Notes.Add("after the run the ETS dispatcher raised " + ex.GetType().Name);
            }
          }
          else
          {
            hostFailure = ex;
          }
        }
      });
      thread.IsBackground = true;
      thread.Name = "ets-sign dispatcher";
      thread.SetApartmentState(ApartmentState.STA);
      thread.Start();

      if (!thread.Join(TimeSpan.FromMinutes(30)))
      {
        return new ExportResult
        {
          Success = false,
          Message = "ETS did not finish within 30 minutes (possible deadlock inside ETS); nothing was written",
        };
      }
      if (hostFailure != null)
      {
        RecordHostFailure(result, hostFailure, secrets);
      }
      result.Secrets = secrets.Snapshot();
      return result;
    }

    private static void Close(ExportSession session, Dispatcher dispatcher, ref int completed, ExportResult result)
    {
      try
      {
        session.Finish();
      }
      catch (Exception ex)
      {
        lock (result.Notes)
        {
          result.Notes.Add("closing the ETS session failed: " + ex.GetType().Name);
        }
      }
      finally
      {
        Volatile.Write(ref completed, 1);
        dispatcher.BeginInvokeShutdown(DispatcherPriority.Background);
      }
    }

    private static void RecordHostFailure(ExportResult result, Exception exception, SecretScanner secrets)
    {
      result.Success = false;
      if (string.IsNullOrEmpty(result.Message))
      {
        result.Message = Text.OneLine(secrets.Redact(exception.Message));
      }
    }
  }

  internal sealed class FailureException : Exception
  {
    public int ExitCode { get; }

    public FailureException(string message, int exitCode = 1) : base(message)
    {
      ExitCode = exitCode;
    }
  }

  internal sealed class EtsInvocationException : Exception
  {
    public EtsInvocationException(Exception inner) : base(inner.Message, inner)
    {
    }
  }

  internal sealed class ExportSession
  {
    private static readonly string[] EtsAssemblies =
    {
      "Knx.Ets.HawkNET", "Knx.Ets.Sdk", "Knx.Ets.ObjectModel", "Knx.Ets.Xml.ObjectModel",
      "Knx.Ets.ViewModel", "Knx.Ets.ObjectModelWindowsBase", "Knx.Ets.Common", "Autofac", "ETS6N", "log4net",
    };

    private readonly ExportRequest _request;
    private readonly ExportResult _result;
    private readonly Dispatcher _dispatcher;
    private readonly Application _application;
    private readonly Progress _progress;
    private readonly SecretScanner _secrets;
    private readonly string _etsDirectory;
    private readonly string _workDirectory;
    private readonly string _storeDirectory;
    private readonly string _inputFull;

    private readonly Dictionary<string, Assembly> _assemblies = new Dictionary<string, Assembly>(StringComparer.Ordinal);
    private readonly List<IDisposable> _disposables = new List<IDisposable>();

    private ContainerBridge _autofac;
    private object _container;
    private object _root;
    private bool _isolatedRootCreated;
    private object _project;
    private object _importSource;
    private object _accessKey;
    private object _keyProviders;
    private object _licence;
    private bool _inputProtected;
    private ArchiveShape _shape;
    private string _previousDirectory;
    private Assembly _signing;
    private IDisposable _extractionFolder;
    private string _extractionRoot;

    public ExportSession(ExportRequest request, ExportResult result, Dispatcher dispatcher,
      Application application, Progress progress, SecretScanner secrets)
    {
      _request = request;
      _result = result;
      _dispatcher = dispatcher;
      _application = application;
      _progress = progress;
      _secrets = secrets;
      _etsDirectory = Path.GetFullPath(request.EtsDirectory).TrimEnd(Path.DirectorySeparatorChar);
      _workDirectory = Path.GetFullPath(request.WorkDirectory).TrimEnd(Path.DirectorySeparatorChar);
      _storeDirectory = Path.Combine(_workDirectory, "ProjectStore");
      _inputFull = Path.GetFullPath(request.InputPath);
    }

    private Assembly A(string key)
    {
      return _assemblies[key];
    }

    public bool RunStartupPhase()
    {
      try
      {
        _progress.Report("loading");
        LoadAssemblies();
        BuildContainer();
        StartupServices();
        CreateRoot();
        return true;
      }
      catch (Exception ex)
      {
        RecordFailure(ex);
        return false;
      }
    }

    public void RunWorkPhase()
    {
      try
      {
        try
        {
          _progress.Report("importing");
          InspectArchive();
          if (_inputProtected)
          {
            UnlockProtectedProject();
          }
          CountInputElements();
          ImportProject();

          _progress.Report("licence");
          ReadLicence();

          _progress.Report("exporting");
          ExportProject();

          _progress.Report("verifying");
          VerifyOutput();

          _result.Success = true;
        }
        finally
        {
          ReleasePassword();
        }
      }
      catch (Exception ex)
      {
        RecordFailure(ex);
      }
    }

    public void Finish()
    {
      try
      {
        Cleanup();
      }
      finally
      {
        ReleasePassword();
        DeleteExtractionFolder();
        if (_previousDirectory != null)
        {
          try { Directory.SetCurrentDirectory(_previousDirectory); }
          catch (Exception) { }
        }
      }
    }

    private void RecordFailure(Exception ex)
    {
      _result.Success = false;
      if (ex is FailureException failure)
      {
        _result.Message = Text.OneLine(_secrets.Redact(failure.Message));
        _result.ExitCode = failure.ExitCode;
      }
      else
      {
        var message = string.IsNullOrEmpty(ex.Message) ? ex.GetType().Name : ex.Message;
        _result.Message = Text.OneLine(_secrets.Redact(message));
        _result.ExitCode = 1;
      }
    }

    private void LoadAssemblies()
    {
      foreach (var name in EtsAssemblies)
      {
        var path = Path.Combine(_etsDirectory, name == "ETS6N" ? "ETS6N.exe" : name + ".dll");
        if (!File.Exists(path))
        {
          throw new FailureException("required ETS file '" + path + "' does not exist", 3);
        }
        var assembly = Assembly.Load(AssemblyName.GetAssemblyName(path));
        if (!IsInEtsDirectory(assembly.Location))
        {
          throw new FailureException(name + " was loaded from outside the ETS directory");
        }
        _assemblies[name] = assembly;
      }
      _previousDirectory = Directory.GetCurrentDirectory();
      Directory.SetCurrentDirectory(_etsDirectory);
    }

    private void BuildContainer()
    {
      var ets6n = A("ETS6N");
      var viewModel = A("Knx.Ets.ViewModel");
      var objectModel = A("Knx.Ets.ObjectModel");
      _autofac = new ContainerBridge();

      var watcher = Reflect.New(Reflect.Ctor(Reflect.Type(ets6n, "ETS.SingleInstanceApplication.SingleInstanceApplicationWatcher")));
      var dispatcherService = Reflect.New(
        Reflect.Ctor(Reflect.Type(viewModel, "Knx.Ets.ViewModel.WpfDispatcherService"), "System.Windows.Threading.Dispatcher"),
        _dispatcher);
      var currentApplication = Reflect.New(
        Reflect.Ctor(Reflect.Type(viewModel, "Knx.Ets.ViewModel.WindowsCurrentApplication"), "System.Windows.Application"),
        _application);

      var rootType = Reflect.Type(objectModel, "Knx.Ets.ObjectModel.Root");
      var rootInterface = Reflect.Type(objectModel, "Knx.Ets.ObjectModel.IRoot");
      var registration = _autofac.RegistrationAction(rootType, new[] { rootType, rootInterface }, CreateIsolatedRoot);

      var build = Reflect.Method(Reflect.Type(ets6n, "ETS.Startup"), "Build", true,
        "*", "*", "*", "System.Action<Autofac.ContainerBuilder>");
      _container = Reflect.Call(build, null, watcher, dispatcherService, currentApplication, registration)
        ?? throw new FailureException("ETS.Startup.Build returned null");
      if (_container is IDisposable disposable)
      {
        _disposables.Add(disposable);
      }
    }

    private object CreateIsolatedRoot(object context)
    {
      Directory.CreateDirectory(_storeDirectory);
      var dataStore = _autofac.ResolveWith(context, Reflect.Type("Knx.Ets.ObjectModel.IDataStore"), typeof(string), _storeDirectory);
      var factory = _autofac.Resolve(context, Reflect.Type("Knx.Ets.ObjectModel.IFactory"));
      var dispatcher = _autofac.Resolve(context, Reflect.Type("Knx.Ets.Common.Wpf.Contracts.IDispatcher"));

      var usageDirectory = Path.Combine(_workDirectory, "Usage");
      Directory.CreateDirectory(usageDirectory);
      var guidWriter = _autofac.Resolve(context, Reflect.Type("Knx.Ets.ObjectModel.FileSystemStore.IInstallationGuidWriter"));
      var usage = Reflect.New(
        Reflect.Ctor(Reflect.Type("Knx.Ets.ObjectModel.Usages.UsageDataStore"),
          "System.String", "Knx.Ets.ObjectModel.FileSystemStore.IInstallationGuidWriter"),
        usageDirectory, guidWriter);

      var root = Reflect.New(
        Reflect.Ctor(Reflect.Type("Knx.Ets.ObjectModel.Root"),
          "Knx.Ets.ObjectModel.IDataStore", "Knx.Ets.ObjectModel.IFactory",
          "Knx.Ets.Common.Wpf.Contracts.IDispatcher", "Knx.Ets.ObjectModel.Usages.IUsageDataStore"),
        dataStore, factory, dispatcher, usage);
      _isolatedRootCreated = true;
      return root;
    }

    private void StartupServices()
    {
      var viewModel = A("Knx.Ets.ViewModel");
      Reflect.New(Reflect.Ctor(Reflect.Type(viewModel, "Knx.Ets.ViewModel.ContainerService"), "Autofac.IComponentContext"), _container);

      var dispatcher = _autofac.Resolve(_container, Reflect.Type("Knx.Ets.Common.Wpf.Contracts.IDispatcher"));
      Reflect.SetStatic(Reflect.Type(A("Knx.Ets.ObjectModelWindowsBase"), "Knx.Ets.ObjectModelWindowsBase.WeakEventListenerProxy"),
        "CurrentDispatcher", dispatcher);

      _autofac.Resolve(_container, Reflect.Type("Knx.Ets.ViewModel.Application.Monitor.GroupValueControl.IDatapointTypeService"));

      try
      {
        var telemetryType = Reflect.Type(viewModel, "Knx.Ets.ViewModel.Metrics.TelemetryConfiguration");
        var telemetry = _autofac.Resolve(_container, telemetryType);
        Reflect.Call(Reflect.Method(telemetryType, "DisableTelemetry", false), telemetry);
      }
      catch (Exception) { }
    }

    private void CreateRoot()
    {
      var rootType = Reflect.Type(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.Root");
      _root = _autofac.Resolve(_container, rootType);
      if (!_isolatedRootCreated)
      {
        throw new FailureException("the ETS container did not use the isolated root; stopping to protect the user's project store");
      }
      var dataStore = Reflect.Get(_root, "DataStore") ?? throw new FailureException("Root.DataStore is null");
      object pathValue;
      if (!Reflect.TryGet(dataStore, "DataStorePath", out pathValue) || !(pathValue is string storePath))
      {
        throw new FailureException("the ETS root reports no DataStorePath; its isolation cannot be verified, stopping");
      }
      if (!PathEquals(storePath, _storeDirectory))
      {
        throw new FailureException("the ETS root uses the store '" + storePath + "' instead of the isolated one; stopping");
      }
    }

    private void InspectArchive()
    {
      _shape = ArchiveShape.Inspect(_inputFull);
      if (_shape.ProjectCount != 1)
      {
        throw new FailureException("the file contains " + _shape.ProjectCount + " project(s); exactly one is required", 2);
      }
      var store = Reflect.New(
        Reflect.Ctor(Reflect.Type(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.ZipContainerStore.ZipContainerDataStore"),
          "System.IO.FileInfo", "System.Func<System.String>"),
        new FileInfo(_inputFull), null);
      if (store is IDisposable disposable)
      {
        _disposables.Add(disposable);
      }
      var protectedProjects = (IEnumerable)(Reflect.Get(store, "PasswordProtectedProjects")
        ?? throw new FailureException("ZipContainerDataStore.PasswordProtectedProjects is null"));
      var count = 0;
      foreach (var item in protectedProjects)
      {
        count++;
      }
      _inputProtected = count > 0;
      _result.OutputProtected = _inputProtected;
      if (!_inputProtected)
      {
        _importSource = store;
      }
      else if (string.IsNullOrEmpty(_request.Password))
      {
        throw new FailureException("the project is password protected; the project password is needed", 5);
      }
    }

    private void UnlockProtectedProject()
    {
      var common = A("Knx.Ets.Common");
      var providersObject = Reflect.Call(
        Reflect.Method(Reflect.Type(common, "Knx.Ets.Common.Security.KeyProviderHelper"), "GetKeyProvidersForPassword", true, "System.String"),
        null, _request.Password) ?? throw new FailureException("KeyProviderHelper.GetKeyProvidersForPassword returned null");
      var providers = ((IEnumerable)providersObject).Cast<object>().ToList();
      if (providers.Count == 0)
      {
        throw new FailureException("ETS built no key provider from the project password", 5);
      }
      _keyProviders = providersObject;
      _secrets.AddRange(providers.Select(KeyOf));

      var conversionType = Reflect.Type(A("Knx.Ets.ViewModel"), "Knx.Ets.ViewModel.Application.Wizards.ImportWizard.IProjectConversionService");
      var conversion = _autofac.Resolve(_container, conversionType);

      var tempFolderCtor = Reflect.Ctor(Reflect.Type(common, "Knx.Ets.Common.Utils.TempFolder"), "System.String", "System.Boolean", "System.String");
      var tempFolderArgs = tempFolderCtor.GetParameters().Select(p => p.HasDefaultValue ? p.DefaultValue : null).ToArray();
      var tempFolder = Reflect.New(tempFolderCtor, tempFolderArgs);
      _extractionFolder = tempFolder as IDisposable;
      _extractionRoot = ((DirectoryInfo)(Reflect.Get(tempFolder, "Root") ?? throw new FailureException("TempFolder.Root is null"))).FullName;
      if (!IsInside(_extractionRoot, _workDirectory))
      {
        throw new FailureException("ETS created its temporary folder outside the isolated work directory; stopping");
      }

      var extract = Reflect.Call(
        Reflect.Method(conversion.GetType(), "ExtractProtectedProjectExportToTemporaryDirectory", false,
          "System.String",
          "System.Collections.Generic.IEnumerable<Knx.Ets.Common.Security.IKeyProvider>",
          "Knx.Ets.Common.Utils.TempFolder"),
        conversion, _inputFull, providersObject, tempFolder)
        ?? throw new FailureException("ExtractProtectedProjectExportToTemporaryDirectory returned null");
      if (IsFailed(extract))
      {
        throw new FailureException("ETS rejected the project password (" + ErrorsOf(extract) + ")", 5);
      }
      foreach (var id in _shape.ProtectedProjectIds)
      {
        if (!Directory.Exists(Path.Combine(_extractionRoot, id)))
        {
          throw new FailureException("ETS reported success but did not extract the project folder " + id);
        }
      }

      _importSource = Reflect.Call(
        Reflect.Method(conversion.GetType(), "GetDataStoreForTempDirectory", false, "Knx.Ets.Common.Utils.TempFolder"),
        conversion, tempFolder) ?? throw new FailureException("GetDataStoreForTempDirectory returned null");
      if (_importSource is IDisposable disposable)
      {
        _disposables.Add(disposable);
      }

      var accessInterface = Reflect.Type(common, "Knx.Ets.Common.Security.IAccessKeyProvider");
      _accessKey = providers.FirstOrDefault(p => accessInterface.IsInstanceOfType(p))
        ?? throw new FailureException("ETS built no IAccessKeyProvider from the project password");
    }

    private void CountInputElements()
    {
      Stream stream = null;
      ZipArchive archive = null;
      try
      {
        if (_extractionRoot != null && _shape.IsProtected)
        {
          var path = Path.Combine(_extractionRoot, _shape.ProtectedProjectIds[0], "0.xml");
          if (File.Exists(path))
          {
            stream = File.OpenRead(path);
          }
        }
        else
        {
          archive = ZipFile.OpenRead(_inputFull);
          var entry = archive.Entries
            .Where(e => e.FullName.Replace('\\', '/').EndsWith("/0.xml", StringComparison.OrdinalIgnoreCase)
              && e.FullName.StartsWith("P-", StringComparison.OrdinalIgnoreCase))
            .OrderBy(e => e.FullName, StringComparer.Ordinal)
            .FirstOrDefault();
          if (entry != null)
          {
            stream = entry.Open();
          }
        }
        if (stream == null)
        {
          return;
        }
        int devices, groups;
        CountElements(stream, out devices, out groups);
        _result.InputDevices = devices;
        _result.InputGroupAddresses = groups;
      }
      catch (Exception)
      {
        _result.InputDevices = -1;
      }
      finally
      {
        stream?.Dispose();
        archive?.Dispose();
      }
    }

    private static void CountElements(Stream stream, out int devices, out int groups)
    {
      devices = 0;
      groups = 0;
      var settings = new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit };
      using (var reader = XmlReader.Create(stream, settings))
      {
        while (reader.Read())
        {
          if (reader.NodeType != XmlNodeType.Element)
          {
            continue;
          }
          if (reader.LocalName == "DeviceInstance")
          {
            devices++;
          }
          else if (reader.LocalName == "GroupAddress")
          {
            groups++;
          }
        }
      }
    }

    private void ImportProject()
    {
      var source = _importSource ?? throw new FailureException("no ETS data store to import from");
      var projects = Reflect.Get(_root, "Projects") ?? throw new FailureException("Root.Projects is null");
      var import = projects.GetType()
        .GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
        .FirstOrDefault(m => m.Name == "Import" && m.GetParameters().Length == 12
          && Reflect.Format(m.GetParameters()[0].ParameterType) == "Knx.Ets.ObjectModel.IDataStore")
        ?? throw new FailureException("ProjectCollection.Import(IDataStore, ...) with 12 parameters was not found");

      var parameters = import.GetParameters();
      var arguments = new object[parameters.Length];
      var boundKey = false;
      for (var i = 0; i < parameters.Length; i++)
      {
        var type = parameters[i].ParameterType;
        if (i == 0)
        {
          arguments[i] = source;
        }
        else if (type == typeof(DateTime))
        {
          arguments[i] = File.GetLastWriteTimeUtc(_inputFull);
        }
        else if (typeof(Delegate).IsAssignableFrom(type))
        {
          arguments[i] = ImportCallback(parameters[i]);
        }
        else if (Reflect.Format(type) == "Knx.Ets.Common.Security.IAccessKeyProvider")
        {
          arguments[i] = _accessKey;
          boundKey = _accessKey != null;
        }
        else
        {
          arguments[i] = null;
        }
      }
      if (_inputProtected && !boundKey)
      {
        throw new FailureException("ProjectCollection.Import has no IAccessKeyProvider parameter for the protected project");
      }

      var imported = new List<object>();
      foreach (var item in (IEnumerable)(Reflect.Call(import, projects, arguments)
        ?? throw new FailureException("ProjectCollection.Import returned null")))
      {
        imported.Add(item);
      }
      if (imported.Count != 1)
      {
        throw new FailureException("ETS imported " + imported.Count + " projects from the file; exactly one is required");
      }
      _project = imported[0];
      DeleteExtractionFolder();
    }

    private Delegate ImportCallback(ParameterInfo parameter)
    {
      var type = parameter.ParameterType;
      var invoke = type.GetMethod("Invoke");
      var name = parameter.Name ?? "callback";
      if (name == "progressCallback")
      {
        return Reflect.ConstantDelegate(type, null);
      }
      if (invoke.ReturnType == typeof(bool) && invoke.GetParameters().Length == 1
        && Reflect.Format(invoke.GetParameters()[0].ParameterType) == "Knx.Ets.ObjectModel.IProjectStore")
      {
        return Reflect.ConstantDelegate(type, true);
      }
      var result = invoke.ReturnType == typeof(bool) ? (object)false : null;
      return Reflect.ConstantDelegate(type, result);
    }

    private void ReadLicence()
    {
      var managerType = Reflect.Type(A("Knx.Ets.ViewModel"), "Knx.Ets.ViewModel.Interfaces.ILicenseAndAppManager");
      var manager = _autofac.Resolve(_container, managerType);
      Reflect.Call(Reflect.Method(managerType, "WaitForDongleManager", false, "System.Int32"), manager, 30000);

      var perProject = Convert.ToInt32(Reflect.Get(manager, "LicensedDevicesPerProject"), CultureInfo.InvariantCulture);
      var withoutLicence = Convert.ToInt32(Reflect.Get(manager, "MaxDevicesWithoutLicense"), CultureInfo.InvariantCulture);
      var projectDevices = Convert.ToInt32(Reflect.Get(_project, "DeviceCount"), CultureInfo.InvariantCulture);

      object chosen = null;
      long best = withoutLicence;
      foreach (var dongle in (IEnumerable)(Reflect.Get(manager, "Dongles") ?? new object[0]))
      {
        foreach (var entry in (IEnumerable)(Reflect.Get(dongle, "Licenses") ?? new object[0]))
        {
          var licence = Reflect.Get(entry, "License");
          if (licence == null)
          {
            continue;
          }
          var expired = Reflect.Get(licence, "IsExpired");
          if (expired is bool flag && flag)
          {
            continue;
          }
          var capacity = FeatureValue(licence, "DeviceCount");
          if (capacity > best)
          {
            chosen = licence;
            best = capacity;
          }
        }
      }

      _licence = chosen;
      if (chosen != null)
      {
        _result.LicensedDevices = (int)best;
        _result.LicenseName = ReadFirst(chosen, "Name", "LicenseName", "CustomerName", "RegisteredTo", "OwnerName", "PackageName");
        _result.LicenseNumber = ReadFirst(chosen, "LicenseNumber", "Number", "RegistrationNumber", "SerialNumber", "LicenseId", "Id");
      }
      else
      {
        _result.LicensedDevices = 0;
        _result.Notes.Add("no ETS licence was found; start ETS 6 once and wait until it shows your licence, then retry. Without a licence ETS caps the import at 5 devices.");
      }

      if (perProject != 65535 && projectDevices > perProject)
      {
        throw new FailureException("the project has " + projectDevices + " devices, but ETS reports a licence for "
          + perProject + " devices per project. " + (chosen == null
            ? "ETS reports no licence at all: start ETS 6 once, wait until it shows your licence, and try again."
            : "A licence with more devices per project is needed."));
      }
    }

    private long FeatureValue(object licence, string name)
    {
      var features = Reflect.Get(licence, "Features");
      if (features == null)
      {
        return -1;
      }
      foreach (var feature in (IEnumerable)features)
      {
        if (Reflect.Get(feature, "Key") as string == name)
        {
          var value = Reflect.Get(Reflect.Get(feature, "Value"), "Value");
          return value == null ? -1 : Convert.ToInt64(value, CultureInfo.InvariantCulture);
        }
      }
      return -1;
    }

    private string ReadFirst(object target, params string[] names)
    {
      foreach (var name in names)
      {
        object value;
        if (Reflect.TryGet(target, name, out value) && value != null)
        {
          var text = value as string ?? Convert.ToString(value, CultureInfo.InvariantCulture);
          if (!string.IsNullOrEmpty(text))
          {
            return text;
          }
        }
      }
      return "";
    }

    private void ExportProject()
    {
      var project = _project;
      var protectedFlag = Reflect.Get(project, "IsPasswordProtected") is bool isProtected && isProtected;

      var open = project.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public)
        .FirstOrDefault(m => m.Name == "Open" && m.GetParameters().Length == 1
          && Reflect.Format(m.GetParameters()[0].ParameterType) == "Knx.Ets.Common.Security.IKeyProvider")
        ?? throw new FailureException("Project.Open(IKeyProvider) was not found");

      object openKey = null;
      if (protectedFlag)
      {
        if (_keyProviders == null)
        {
          throw new FailureException("ETS reports the imported project as password protected, but no project password was given", 5);
        }
        var access = Reflect.Call(
          Reflect.Method(project.GetType(), "CheckAccess", false,
            "System.Collections.Generic.IEnumerable<Knx.Ets.Common.Security.IKeyProvider>", "System.Boolean"),
          project, _keyProviders, true) ?? throw new FailureException("Project.CheckAccess returned null");
        if (IsFailed(access))
        {
          throw new FailureException("ETS rejected the project password when opening the project (" + ErrorsOf(access) + ")", 5);
        }
        openKey = Reflect.Get(access, "ValueOrDefault") ?? throw new FailureException("Project.CheckAccess accepted no key");
      }

      Reflect.Call(open, project, openKey);
      if (Reflect.Get(project, "IsOpen") is bool opened && !opened)
      {
        throw new FailureException("Project.Open returned but the project is not open");
      }

      var installation = Reflect.Get(project, "DefaultInstallation") ?? throw new FailureException("Project.DefaultInstallation is null");
      _result.Devices = Count(Reflect.Get(installation, "AllDevices") ?? throw new FailureException("Installation.AllDevices is null"));
      _result.GroupAddresses = Count(Reflect.Get(installation, "AllGroupAddresses"));

      if (_result.InputDevices >= 0 && _result.InputDevices != _result.Devices)
      {
        throw new FailureException("ETS imported " + _result.Devices + " of the " + _result.InputDevices
          + " devices of the file; the export would be incomplete. Nothing was written");
      }
      CheckTopology(installation);

      var outputPath = Path.Combine(_workDirectory, "out", "export.knxproj");
      Directory.CreateDirectory(Path.GetDirectoryName(outputPath));
      if (File.Exists(outputPath))
      {
        throw new FailureException("the work output file exists already");
      }

      var save = Reflect.Method(project.GetType(), "Save", false, "System.Boolean");
      OnDispatcher(() => Reflect.Call(save, project, true));

      var exporterType = Reflect.Type(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.Import.IExporter");
      var exporter = _autofac.Resolve(_container, exporterType);
      var export = exporterType.GetMethods().FirstOrDefault(m => m.Name == "ExportZipped")
        ?? throw new FailureException("IExporter.ExportZipped was not found");
      var parameters = export.GetParameters();
      var arguments = new object[parameters.Length];
      arguments[0] = project;
      arguments[1] = outputPath;
      var exportKey = _inputProtected
        ? (_accessKey ?? throw new FailureException("the input was protected, but ETS's access key is no longer available"))
        : null;
      for (var i = 2; i < parameters.Length; i++)
      {
        var formatted = Reflect.Format(parameters[i].ParameterType);
        if (formatted == "Knx.Ets.Common.Security.IKeyProvider")
        {
          arguments[i] = exportKey;
        }
        else if (formatted == "Knx.Ets.Common.Licensing.License")
        {
          arguments[i] = _licence;
        }
        else
        {
          arguments[i] = null;
        }
      }

      try
      {
        OnDispatcher(() => Reflect.Call(export, exporter, arguments));
      }
      catch (EtsInvocationException ex)
      {
        TryDelete(outputPath);
        throw new FailureException("ETS's export failed: " + ex.InnerException.Message);
      }

      if (!File.Exists(outputPath))
      {
        throw new FailureException("ETS's export returned without writing the file");
      }
      _result.OutputProtected = exportKey != null;
      _result.OutputSha256 = Digest.OfFile(outputPath);
      ReadCertificate(outputPath);
    }

    private void ReadCertificate(string file)
    {
      using (var archive = ZipFile.OpenRead(file))
      {
        var hasCertificate = archive.Entries.Any(e => e.FullName.IndexOf('/') < 0
          && e.FullName.StartsWith("P-", StringComparison.OrdinalIgnoreCase)
          && e.FullName.EndsWith(".certificate", StringComparison.OrdinalIgnoreCase));
        if (!hasCertificate)
        {
          return;
        }
        _result.Certificate = true;

        var validation = archive.GetEntry(".validation");
        if (validation != null)
        {
          using (var reader = new StreamReader(validation.Open()))
          {
            var text = reader.ReadToEnd();
            _result.Validation = text.IndexOf("certificate.IsValid:True", StringComparison.OrdinalIgnoreCase) >= 0
              && text.IndexOf("validation succeeded:True", StringComparison.OrdinalIgnoreCase) >= 0;
          }
        }
        if (!_result.Validation)
        {
          _result.Notes.Add("ETS wrote a certificate, but its validation entry does not report it as valid.");
        }
      }
    }

    private void VerifyOutput()
    {
      var file = Path.Combine(_workDirectory, "out", "export.knxproj");
      string directory;
      var states = CheckArchive(file, "check-output", out directory);
      if (states.Count == 0)
      {
        throw new FailureException("ETS's export contains no directory to check");
      }
      var invalid = states.Where(s => s.Value != "SignatureFoundAndValid").Select(s => s.Key + ": " + s.Value).ToArray();
      if (invalid.Length != 0)
      {
        throw new FailureException("ETS's signature check does not accept its own export (" + string.Join(", ", invalid) + "); nothing was written");
      }

      var candidates = Directory.GetDirectories(directory).Select(Path.GetFileName)
        .Where(n => n.StartsWith("P-", StringComparison.OrdinalIgnoreCase)).ToList();
      var projectFolder = candidates.FirstOrDefault();
      if (projectFolder != null)
      {
        var installationFile = Path.Combine(directory, projectFolder, "0.xml");
        if (File.Exists(installationFile))
        {
          using (var stream = File.OpenRead(installationFile))
          {
            int devices, groups;
            CountElements(stream, out devices, out groups);
            if (devices != _result.Devices)
            {
              throw new FailureException("ETS's export contains " + devices + " device(s) instead of the "
                + _result.Devices + " it imported; nothing was written");
            }
          }
        }
      }
      TryDeleteDirectory(directory);
    }

    private List<KeyValuePair<string, string>> CheckArchive(string file, string folder, out string directory)
    {
      directory = Path.Combine(_workDirectory, folder);
      Directory.CreateDirectory(directory);
      var extractorType = Reflect.Type(A("Knx.Ets.Common"), "Knx.Ets.Common.Security.EtsZipExtractor");
      var extractor = Reflect.New(Reflect.Ctor(extractorType, "System.String"), file);

      object extract;
      if (ArchiveShape.Inspect(file).IsProtected)
      {
        if (_keyProviders == null)
        {
          throw new FailureException("the archive to check is protected, but no key providers exist", 5);
        }
        extract = Reflect.Call(
          Reflect.Method(extractorType, "Extract", false,
            "System.IO.DirectoryInfo",
            "System.Collections.Generic.IEnumerable<Knx.Ets.Common.Security.IKeyProvider>",
            "System.Boolean"),
          extractor, new DirectoryInfo(directory), _keyProviders, false)
          ?? throw new FailureException("EtsZipExtractor.Extract returned null");
      }
      else
      {
        extract = Reflect.Call(
          Reflect.Method(extractorType, "Extract", false,
            "System.IO.DirectoryInfo", "Knx.Ets.Common.Security.IKeyProvider", "System.Boolean"),
          extractor, new DirectoryInfo(directory), null, false)
          ?? throw new FailureException("EtsZipExtractor.Extract returned null");
      }
      if (IsFailed(extract))
      {
        throw new FailureException("ETS's extractor could not read the archive for the signature check (" + ErrorsOf(extract) + ")");
      }

      var check = Reflect.Method(Reflect.Type(LoadSigningAssembly(), "Knx.Ets.XmlSigning.XmlSigning"),
        "CheckDirectorySignature", true, "System.String", "System.String[]");
      var states = new List<KeyValuePair<string, string>>();
      foreach (var sub in Directory.GetDirectories(directory).OrderBy(d => d, StringComparer.Ordinal))
      {
        var state = Reflect.Call(check, null, sub, null);
        states.Add(new KeyValuePair<string, string>(Path.GetFileName(sub), state?.ToString() ?? "null"));
      }
      return states;
    }

    private Assembly LoadSigningAssembly()
    {
      if (_signing != null)
      {
        return _signing;
      }
      var path = Path.Combine(_etsDirectory, "Knx.Ets.XmlSigning.dll");
      if (!File.Exists(path))
      {
        throw new FailureException("required ETS file '" + path + "' does not exist");
      }
      var assembly = Assembly.Load(AssemblyName.GetAssemblyName(path));
      if (!IsInEtsDirectory(assembly.Location))
      {
        throw new FailureException("Knx.Ets.XmlSigning was loaded from outside the ETS directory");
      }
      _signing = assembly;
      return assembly;
    }

    private void CheckTopology(object installation)
    {
      var backbone = Reflect.Get(installation, "BackboneLine");
      if (backbone == null || !HasSegment(backbone))
      {
        throw new FailureException("the project has no backbone (area 0 with main line 0 and a segment); ETS could not open it. Nothing was written");
      }
      foreach (var area in (IEnumerable)(Reflect.Get(installation, "Areas") ?? throw new FailureException("Installation.Areas is null")))
      {
        var mainLine = Reflect.Get(area, "MainLine");
        if (mainLine == null || !HasSegment(mainLine))
        {
          throw new FailureException("area " + Reflect.Get(area, "Address") + " has no main line (line 0 with a segment); ETS needs it. Nothing was written");
        }
      }
    }

    private static bool HasSegment(object line)
    {
      return Count(Reflect.Get(line, "Segments")) > 0;
    }

    private void Cleanup()
    {
      try
      {
        if (_project != null && Reflect.Get(_project, "IsOpen") is bool open && open)
        {
          Reflect.Call(Reflect.Method(_project.GetType(), "Close", false), _project);
        }
      }
      catch (Exception ex)
      {
        _result.Notes.Add("closing the ETS project failed: " + ex.GetType().Name);
      }
      try
      {
        if (_root != null)
        {
          Reflect.Call(Reflect.Method(_root.GetType(), "Shutdown", false), _root);
        }
      }
      catch (Exception ex)
      {
        _result.Notes.Add("Root.Shutdown failed: " + ex.GetType().Name);
      }
      for (var i = _disposables.Count - 1; i >= 0; i--)
      {
        try { _disposables[i].Dispose(); }
        catch (Exception) { }
      }
    }

    private void ReleasePassword()
    {
      _request.Password = null;
      _keyProviders = null;
      _accessKey = null;
    }

    private void DeleteExtractionFolder()
    {
      if (_extractionFolder == null && _extractionRoot == null)
      {
        return;
      }
      try { _extractionFolder?.Dispose(); }
      catch (Exception) { }
      _extractionFolder = null;
      if (_extractionRoot != null)
      {
        TryDeleteDirectory(_extractionRoot);
      }
      _extractionRoot = null;
    }

    private void OnDispatcher(Action change)
    {
      Exception thrown = null;
      var completed = false;
      try
      {
        _dispatcher.Invoke((Action)(() =>
        {
          try
          {
            change();
            completed = true;
          }
          catch (TargetInvocationException ex) when (ex.InnerException != null)
          {
            thrown = ex.InnerException;
          }
          catch (Exception ex)
          {
            thrown = ex;
          }
        }), DispatcherPriority.Send, CancellationToken.None, TimeSpan.FromMinutes(10));
      }
      catch (TimeoutException)
      {
        throw new FailureException("ETS did not finish within 10 minutes on its dispatcher thread");
      }
      if (thrown != null)
      {
        throw new EtsInvocationException(thrown);
      }
      if (!completed)
      {
        throw new FailureException("ETS's dispatcher did not run the call in time");
      }
    }

    private static int Count(object collection)
    {
      if (collection == null)
      {
        return 0;
      }
      var count = 0;
      foreach (var item in (IEnumerable)collection)
      {
        count++;
      }
      return count;
    }

    private static bool IsFailed(object result)
    {
      return Reflect.Get(result, "IsFailed") is bool failed && failed;
    }

    private string ErrorsOf(object result)
    {
      var messages = new List<string>();
      if (Reflect.Get(result, "Errors") is IEnumerable errors)
      {
        foreach (var error in errors)
        {
          object value;
          var message = Reflect.TryGet(error, "Message", out value) ? value as string : null;
          message = _secrets.Redact(message);
          messages.Add(string.IsNullOrWhiteSpace(message) ? error.GetType().Name : error.GetType().Name + ": " + message);
        }
      }
      return messages.Count != 0 ? string.Join("; ", messages) : "no error reported";
    }

    private static string KeyOf(object provider)
    {
      try
      {
        return Reflect.Get(provider, "Key") as string;
      }
      catch (Exception)
      {
        return null;
      }
    }

    private static void TryDelete(string file)
    {
      try { if (File.Exists(file)) File.Delete(file); }
      catch (Exception) { }
    }

    private static void TryDeleteDirectory(string directory)
    {
      try { if (Directory.Exists(directory)) Directory.Delete(directory, true); }
      catch (Exception) { }
    }

    private bool IsInEtsDirectory(string location)
    {
      return !string.IsNullOrEmpty(location) && IsInside(location, _etsDirectory, true);
    }

    private static bool IsInside(string path, string directory)
    {
      return IsInside(path, directory, false);
    }

    private static bool IsInside(string path, string directory, bool includeExact)
    {
      var full = Path.GetFullPath(path);
      var root = directory.TrimEnd(Path.DirectorySeparatorChar);
      if (includeExact && string.Equals(full.TrimEnd(Path.DirectorySeparatorChar), root, StringComparison.OrdinalIgnoreCase))
      {
        return true;
      }
      return full.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
    }

    private static bool PathEquals(string a, string b)
    {
      return string.Equals(
        Path.GetFullPath(a).TrimEnd(Path.DirectorySeparatorChar),
        Path.GetFullPath(b).TrimEnd(Path.DirectorySeparatorChar),
        StringComparison.OrdinalIgnoreCase);
    }
  }

  internal static class Reflect
  {
    private const BindingFlags AnyInstance = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    private const BindingFlags AnyStatic = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;

    public static Type Type(string fullName)
    {
      foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
      {
        var type = assembly.GetType(fullName, false);
        if (type != null)
        {
          return type;
        }
      }
      throw new FailureException("ETS type " + fullName + " was not found");
    }

    public static Type Type(Assembly assembly, string fullName)
    {
      return assembly.GetType(fullName, false) ?? throw new FailureException("ETS type " + fullName + " was not found in " + assembly.GetName().Name);
    }

    public static ConstructorInfo Ctor(Type type, params string[] parameterTypes)
    {
      return type.GetConstructors(AnyInstance).FirstOrDefault(c => Matches(c.GetParameters(), parameterTypes))
        ?? throw new FailureException("constructor " + type.FullName + "(" + string.Join(", ", parameterTypes) + ") was not found");
    }

    public static MethodInfo Method(Type type, string name, bool isStatic, params string[] parameterTypes)
    {
      var flags = (isStatic ? AnyStatic : AnyInstance) | BindingFlags.DeclaredOnly;
      for (var current = type; current != null; current = current.BaseType)
      {
        var method = current.GetMethods(flags).FirstOrDefault(m => m.Name == name
          && !m.IsGenericMethodDefinition && Matches(m.GetParameters(), parameterTypes));
        if (method != null)
        {
          return method;
        }
      }
      throw new FailureException("method " + type.FullName + "." + name + "(" + string.Join(", ", parameterTypes) + ") was not found");
    }

    public static object New(ConstructorInfo constructor, params object[] arguments)
    {
      try
      {
        return constructor.Invoke(arguments);
      }
      catch (TargetInvocationException ex) when (ex.InnerException != null)
      {
        ExceptionDispatchInfo.Capture(ex.InnerException).Throw();
        throw;
      }
    }

    public static object Call(MethodInfo method, object target, params object[] arguments)
    {
      try
      {
        return method.Invoke(target, arguments);
      }
      catch (TargetInvocationException ex) when (ex.InnerException != null)
      {
        ExceptionDispatchInfo.Capture(ex.InnerException).Throw();
        throw;
      }
    }

    public static object Get(object target, string name)
    {
      for (var type = target.GetType(); type != null; type = type.BaseType)
      {
        var property = type.GetProperty(name, BindingFlags.DeclaredOnly | AnyInstance);
        if (property != null && property.GetIndexParameters().Length == 0)
        {
          try
          {
            return property.GetValue(target, null);
          }
          catch (TargetInvocationException ex) when (ex.InnerException != null)
          {
            ExceptionDispatchInfo.Capture(ex.InnerException).Throw();
            throw;
          }
        }
        var field = type.GetField(name, BindingFlags.DeclaredOnly | AnyInstance);
        if (field != null)
        {
          return field.GetValue(target);
        }
      }
      throw new FailureException("member " + name + " was not found on " + target.GetType().FullName);
    }

    public static bool TryGet(object target, string name, out object value)
    {
      for (var type = target.GetType(); type != null; type = type.BaseType)
      {
        if (type.GetProperty(name, BindingFlags.DeclaredOnly | AnyInstance) != null
          || type.GetField(name, BindingFlags.DeclaredOnly | AnyInstance) != null)
        {
          value = Get(target, name);
          return true;
        }
      }
      value = null;
      return false;
    }

    public static void SetStatic(Type type, string name, object value)
    {
      var property = type.GetProperty(name, AnyStatic);
      if (property != null && property.CanWrite)
      {
        property.SetValue(null, value, null);
        return;
      }
      var field = type.GetField(name, AnyStatic) ?? throw new FailureException("static member " + type.FullName + "." + name + " was not found");
      field.SetValue(null, value);
    }

    public static Delegate ConstantDelegate(Type delegateType, object result)
    {
      var invoke = delegateType.GetMethod("Invoke") ?? throw new FailureException(delegateType + " is not a delegate type");
      var parameters = invoke.GetParameters().Select(p => Expression.Parameter(p.ParameterType, p.Name)).ToArray();
      Expression body = invoke.ReturnType == typeof(void)
        ? (Expression)Expression.Empty()
        : Expression.Constant(result, invoke.ReturnType);
      return Expression.Lambda(delegateType, body, parameters).Compile();
    }

    public static string Format(Type type)
    {
      if (type.IsByRef)
      {
        return Format(type.GetElementType()) + "&";
      }
      if (type.IsArray)
      {
        return Format(type.GetElementType()) + "[]";
      }
      if (!type.IsGenericType)
      {
        return type.FullName ?? type.Name;
      }
      var definition = type.GetGenericTypeDefinition();
      var name = definition.FullName ?? definition.Name;
      var tick = name.IndexOf('`');
      if (tick >= 0)
      {
        name = name.Substring(0, tick);
      }
      return name + "<" + string.Join(",", type.GetGenericArguments().Select(Format)) + ">";
    }

    private static bool Matches(ParameterInfo[] parameters, string[] expected)
    {
      if (parameters.Length != expected.Length)
      {
        return false;
      }
      for (var i = 0; i < parameters.Length; i++)
      {
        if (expected[i] != "*" && !string.Equals(Format(parameters[i].ParameterType), expected[i], StringComparison.Ordinal))
        {
          return false;
        }
      }
      return true;
    }
  }

  internal sealed class ContainerBridge
  {
    private readonly Type _componentContext;
    private readonly Type _parameter;
    private readonly ConstructorInfo _typedParameter;
    private readonly MethodInfo _resolve;
    private readonly Type _containerBuilder;
    private readonly MethodInfo _register;

    public ContainerBridge()
    {
      _componentContext = Reflect.Type("Autofac.IComponentContext");
      _parameter = Reflect.Type("Autofac.Core.Parameter");
      _typedParameter = Reflect.Ctor(Reflect.Type("Autofac.TypedParameter"), "System.Type", "System.Object");
      _resolve = Reflect.Method(Reflect.Type("Autofac.ResolutionExtensions"), "Resolve", true,
        "Autofac.IComponentContext", "System.Type", "Autofac.Core.Parameter[]");
      _containerBuilder = Reflect.Type("Autofac.ContainerBuilder");
      _register = Reflect.Type("Autofac.RegistrationExtensions")
        .GetMethods(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic)
        .FirstOrDefault(m => m.Name == "Register" && m.IsGenericMethodDefinition
          && m.GetGenericArguments().Length == 1 && m.GetParameters().Length == 2
          && m.GetParameters()[1].ParameterType.IsGenericType
          && m.GetParameters()[1].ParameterType.GetGenericTypeDefinition() == typeof(Func<,>))
        ?? throw new FailureException("Autofac.RegistrationExtensions.Register<T>(ContainerBuilder, Func<IComponentContext, T>) was not found");
    }

    public object Resolve(object context, Type service)
    {
      var empty = Array.CreateInstance(_parameter, 0);
      return Reflect.Call(_resolve, null, context, service, empty)
        ?? throw new FailureException("the ETS container resolved " + service.FullName + " to null");
    }

    public object ResolveWith(object context, Type service, Type parameterType, object parameterValue)
    {
      var parameters = Array.CreateInstance(_parameter, 1);
      parameters.SetValue(Reflect.New(_typedParameter, parameterType, parameterValue), 0);
      return Reflect.Call(_resolve, null, context, service, parameters)
        ?? throw new FailureException("the ETS container resolved " + service.FullName + " to null");
    }

    public Delegate RegistrationAction(Type implementation, Type[] services, Func<object, object> factory)
    {
      var builder = Expression.Parameter(_containerBuilder, "builder");
      Action<object> body = target => RegisterSingleInstance(target, implementation, services, factory);
      var invoke = Expression.Invoke(Expression.Constant(body), Expression.Convert(builder, typeof(object)));
      var actionType = typeof(Action<>).MakeGenericType(_containerBuilder);
      return Expression.Lambda(actionType, invoke, builder).Compile();
    }

    private void RegisterSingleInstance(object builder, Type implementation, Type[] services, Func<object, object> factory)
    {
      var context = Expression.Parameter(_componentContext, "context");
      Expression body = Expression.Convert(
        Expression.Invoke(Expression.Constant(factory), Expression.Convert(context, typeof(object))),
        implementation);
      var funcType = typeof(Func<,>).MakeGenericType(_componentContext, implementation);
      var func = Expression.Lambda(funcType, body, context).Compile();

      var registration = _register.MakeGenericMethod(implementation).Invoke(null, new object[] { builder, func })
        ?? throw new FailureException("Autofac Register returned null");
      registration = FindBuilderMethod(registration, "As", typeof(Type[])).Invoke(registration, new object[] { services });
      FindBuilderMethod(registration, "SingleInstance").Invoke(registration, null);
    }

    private static MethodInfo FindBuilderMethod(object target, string name, params Type[] parameterTypes)
    {
      foreach (var type in new[] { target.GetType() }.Concat(target.GetType().GetInterfaces()))
      {
        var method = type.GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
          .FirstOrDefault(m => m.Name == name && !m.IsGenericMethodDefinition
            && m.GetParameters().Select(p => p.ParameterType).SequenceEqual(parameterTypes));
        if (method != null)
        {
          return method;
        }
      }
      throw new FailureException("Autofac registration method " + name + " was not found on " + target.GetType().FullName);
    }
  }

  internal sealed class ArchiveShape
  {
    private static readonly Regex ProtectedStore = new Regex("^(P-[0-9A-Fa-f]{4})\\.zip$", RegexOptions.CultureInvariant);
    private static readonly Regex PlainStore = new Regex("^(P-[0-9A-Fa-f]{4})/", RegexOptions.CultureInvariant);

    public IReadOnlyList<string> ProtectedProjectIds { get; }
    public IReadOnlyList<string> PlainProjectIds { get; }

    public int ProjectCount => ProtectedProjectIds.Count + PlainProjectIds.Count;
    public bool IsProtected => ProtectedProjectIds.Count > 0;

    private ArchiveShape(IEnumerable<string> protectedIds, IEnumerable<string> plainIds)
    {
      ProtectedProjectIds = protectedIds.Distinct(StringComparer.OrdinalIgnoreCase).OrderBy(i => i, StringComparer.Ordinal).ToList();
      PlainProjectIds = plainIds.Distinct(StringComparer.OrdinalIgnoreCase).OrderBy(i => i, StringComparer.Ordinal).ToList();
    }

    public static ArchiveShape Inspect(string path)
    {
      using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
      using (var archive = new ZipArchive(stream, ZipArchiveMode.Read))
      {
        var protectedIds = new List<string>();
        var plainIds = new List<string>();
        foreach (var entry in archive.Entries)
        {
          var normalized = entry.FullName.Replace('\\', '/');
          var match = ProtectedStore.Match(normalized);
          if (match.Success)
          {
            protectedIds.Add(match.Groups[1].Value);
            continue;
          }
          var plain = PlainStore.Match(normalized);
          if (plain.Success)
          {
            plainIds.Add(plain.Groups[1].Value);
          }
        }
        return new ArchiveShape(protectedIds, plainIds);
      }
    }
  }

  internal sealed class SecretScanner
  {
    private readonly List<string> _secrets;

    public SecretScanner(IEnumerable<string> secrets)
    {
      _secrets = secrets.Where(s => !string.IsNullOrEmpty(s)).Distinct(StringComparer.Ordinal)
        .OrderByDescending(s => s.Length).ToList();
    }

    public void AddRange(IEnumerable<string> secrets)
    {
      foreach (var secret in secrets.Where(s => !string.IsNullOrEmpty(s)))
      {
        if (!_secrets.Contains(secret, StringComparer.Ordinal))
        {
          _secrets.Add(secret);
        }
      }
      _secrets.Sort((a, b) => b.Length.CompareTo(a.Length));
    }

    public string[] Snapshot()
    {
      return _secrets.ToArray();
    }

    public string Redact(string text)
    {
      if (text == null || _secrets.Count == 0)
      {
        return text;
      }
      foreach (var secret in _secrets)
      {
        if (text.IndexOf(secret, StringComparison.Ordinal) >= 0)
        {
          text = text.Replace(secret, "<redacted>");
        }
      }
      return text;
    }

    public bool ContainsSecret(byte[] content)
    {
      foreach (var secret in _secrets)
      {
        if (IndexOf(content, Encoding.UTF8.GetBytes(secret)) >= 0
          || IndexOf(content, Encoding.Unicode.GetBytes(secret)) >= 0)
        {
          return true;
        }
      }
      return false;
    }

    private static int IndexOf(byte[] haystack, byte[] needle)
    {
      if (needle.Length == 0 || needle.Length > haystack.Length)
      {
        return -1;
      }
      for (var i = 0; i <= haystack.Length - needle.Length; i++)
      {
        var j = 0;
        while (j < needle.Length && haystack[i + j] == needle[j])
        {
          j++;
        }
        if (j == needle.Length)
        {
          return i;
        }
      }
      return -1;
    }
  }

  internal static class Digest
  {
    public static string OfFile(string path)
    {
      using (var sha = System.Security.Cryptography.SHA256.Create())
      using (var stream = File.OpenRead(path))
      {
        return Hex(sha.ComputeHash(stream));
      }
    }

    public static string OfBytes(byte[] content)
    {
      using (var sha = System.Security.Cryptography.SHA256.Create())
      {
        return Hex(sha.ComputeHash(content));
      }
    }

    private static string Hex(byte[] value)
    {
      var builder = new StringBuilder(value.Length * 2);
      foreach (var b in value)
      {
        builder.Append(b.ToString("x2", CultureInfo.InvariantCulture));
      }
      return builder.ToString();
    }
  }

  internal static class StoreFingerprint
  {
    public static string[] Take()
    {
      var programData = Environment.GetEnvironmentVariable("ProgramData") ?? "C:\\ProgramData";
      var localAppData = Environment.GetEnvironmentVariable("LOCALAPPDATA") ?? "";
      var locations = new List<string>
      {
        Path.Combine(programData, "KNX", "ETS6", "ProductStore"),
        Path.Combine(programData, "KNX", "ETS6", "ProjectStore"),
      };
      if (localAppData.Length > 0)
      {
        locations.Add(Path.Combine(localAppData, "KNX", "ETS6", "Cache"));
        locations.Add(Path.Combine(localAppData, "KNX", "ETS6", "My Products"));
      }

      var result = new List<string>();
      foreach (var location in locations)
      {
        if (!Directory.Exists(location))
        {
          result.Add(location + " missing");
          continue;
        }
        long files = 0;
        long bytes = 0;
        var newest = DateTime.MinValue;
        try
        {
          foreach (var file in Directory.EnumerateFiles(location, "*", SearchOption.AllDirectories))
          {
            try
            {
              var info = new FileInfo(file);
              files++;
              bytes += info.Length;
              if (info.LastWriteTimeUtc > newest)
              {
                newest = info.LastWriteTimeUtc;
              }
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException) { }
          }
        }
        catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
        {
          result.Add(location + " unreadable");
          continue;
        }
        result.Add(location + " files=" + files + " bytes=" + bytes + " newest="
          + (files == 0 ? "none" : newest.ToString("o", CultureInfo.InvariantCulture)));
      }
      return result.ToArray();
    }

    public static bool Same(string[] before, string[] after)
    {
      return before.SequenceEqual(after, StringComparer.Ordinal);
    }
  }
}
