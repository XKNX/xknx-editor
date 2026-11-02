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

namespace Ets5SignExport
{
  internal static class Program
  {
    private const string ExecutableName = "ets5n.exe";
    private const string ConfigName = "ETS5N.exe.config";

    // 5.7.1428 is the build this tool was written against; other 5.7.* need --accept-untested.
    private const int KnownGoodBuild = 1428;

    private static readonly string[] RequiredFiles =
    {
      ExecutableName, ConfigName, "Autofac.dll", "SharpCompress.dll",
      "Knx.Ets.ObjectModel.dll", "Knx.Ets.Xml.ObjectModel.dll", "Knx.Ets.ViewModel.dll",
      "Knx.Ets.UserControls.dll", "Knx.Ets.ObjectModelWindowsBase.dll", "Knx.Ets.XmlSigning.dll",
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
        Error("this tool runs only on Windows with a locally installed, licensed ETS 5");
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
      if (!VersionAllowed(Path.Combine(etsDir, ExecutableName), acceptUntested, out versionError))
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

      var workDir = Path.Combine(Path.GetTempPath(), "ets5-sign-" + Guid.NewGuid().ToString("N"));
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
          ConfigurationFile = Path.Combine(etsDir, ConfigName),
          DisallowCodeDownload = true,
        };
        var domain = AppDomain.CreateDomain("ets5-sign", null, setup,
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

        var notes = new List<string>(result.Notes ?? new List<string>());
        string publishError;
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
        error = "the ETS version could not be read from " + ExecutableName;
        return false;
      }
      if (version.Major != 5 || version.Minor != 7)
      {
        error = "ETS " + token + " is not supported; this tool works with ETS 5.7 only";
        return false;
      }
      if (version.Build == KnownGoodBuild)
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
      thread.Name = "ets5-sign dispatcher";
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
      "Knx.Ets.ObjectModel", "Knx.Ets.Xml.ObjectModel", "Knx.Ets.ViewModel",
      "Knx.Ets.UserControls", "Knx.Ets.ObjectModelWindowsBase", "SharpCompress", "Autofac", "ets5n", "log4net",
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
    private object _licence;
    private bool _inputProtected;
    private ArchiveShape _shape;
    private string _previousDirectory;
    private Assembly _signing;
    private string _stage = "startup";

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
        Stage("loading");
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
          Stage("importing");
          InspectArchive();
          ImportProject();

          Stage("licence");
          ReadLicence();

          Stage("exporting");
          ExportProject();

          Stage("verifying");
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
        if (_previousDirectory != null)
        {
          try { Directory.SetCurrentDirectory(_previousDirectory); }
          catch (Exception) { }
        }
      }
    }

    private void Stage(string stage)
    {
      _stage = stage;
      _progress.Report(stage);
    }

    private void RecordFailure(Exception ex)
    {
      _result.Success = false;
      if (ex is FailureException failure)
      {
        _result.Message = Text.OneLine(_secrets.Redact(failure.Message));
        _result.ExitCode = failure.ExitCode;
        return;
      }
      // A plain exception (often a NullReferenceException deep in ETS, wrapped by reflection) has a
      // generic message. Unwrap to the innermost exception and include the stage + its top stack
      // frames so the failing step is identifiable from the one-line ERROR the GUI shows.
      var inner = ex;
      while (inner.InnerException != null)
      {
        inner = inner.InnerException;
      }
      var frames = (inner.StackTrace ?? "")
        .Split('\n')
        .Select(line => line.Trim())
        .Where(line => line.Length > 0)
        .Take(4);
      var detail = "[" + _stage + "] " + inner.GetType().Name + ": "
        + (string.IsNullOrEmpty(inner.Message) ? "(no message)" : inner.Message)
        + " | " + string.Join(" <- ", frames);
      _result.Message = Text.OneLine(_secrets.Redact(detail));
      _result.ExitCode = 1;
    }

    private void LoadAssemblies()
    {
      foreach (var name in EtsAssemblies)
      {
        var path = Path.Combine(_etsDirectory, name == "ets5n" ? "ets5n.exe" : name + ".dll");
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
      _autofac = new ContainerBridge();
      var builder = _autofac.NewBuilder();

      // A live IDispatcher is the one pre-registration the ObjectModel/ViewModel modules rely on.
      var dispatcherService = Reflect.New(
        Reflect.Ctor(Reflect.Type(A("Knx.Ets.ViewModel"), "Knx.Ets.ViewModel.WpfDispatcherService"), "System.Windows.Threading.Dispatcher"),
        _dispatcher);
      _autofac.RegisterInstance(builder, dispatcherService,
        new[] { Reflect.Type(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.IDispatcher") });

      RegisterUiDispatcher(builder, dispatcherService);

      // ObjectModel + ViewModel modules are the UI-free graph the export/cert path needs; the
      // UserControls module is registered only when this build exposes it.
      _autofac.RegisterModule(builder, Reflect.New(Reflect.Ctor(Reflect.Type(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.IocModule"))));
      _autofac.RegisterModule(builder, Reflect.New(Reflect.Ctor(Reflect.Type(A("Knx.Ets.ViewModel"), "Knx.Ets.ViewModel.IocModule"))));
      var userControlsModule = Reflect.TypeOrNull(A("Knx.Ets.UserControls"), "Knx.Ets.UserControls.IocModule");
      if (userControlsModule != null)
      {
        _autofac.RegisterModule(builder, Reflect.New(Reflect.Ctor(userControlsModule)));
      }

      var rootType = Reflect.Type(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.Root");
      _autofac.RegisterSingleInstance(builder, rootType, new[] { rootType }, CreateIsolatedRoot);

      RegisterLicenceManager(builder);

      _container = _autofac.Build(builder) ?? throw new FailureException("the ETS container could not be built");
      if (_container is IDisposable disposable)
      {
        _disposables.Add(disposable);
      }
    }

    private void RegisterUiDispatcher(object builder, object dispatcherService)
    {
      // The UI dispatcher interface lives in ObjectModel (not ViewModel.Interfaces). Prefer the
      // WpfDispatcherService already built; otherwise an assignable parameterless ets5n type.
      try
      {
        var contract = Reflect.TypeOrNull(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.IUiDispatcher");
        if (contract == null)
        {
          return;
        }
        if (contract.IsAssignableFrom(dispatcherService.GetType()))
        {
          _autofac.RegisterInstance(builder, dispatcherService, new[] { contract });
          return;
        }
        var impl = Reflect.SafeTypes(A("ets5n")).FirstOrDefault(t => !t.IsAbstract && !t.IsInterface
          && contract.IsAssignableFrom(t) && t.GetConstructor(Type.EmptyTypes) != null);
        if (impl != null)
        {
          _autofac.RegisterInstance(builder, Reflect.New(Reflect.Ctor(impl)), new[] { contract });
        }
      }
      catch (Exception) { }
    }

    private void RegisterLicenceManager(object builder)
    {
      var manager = Reflect.Type(A("Knx.Ets.ViewModel"), "Knx.Ets.ViewModel.Interfaces.ILicenseAndAppManager");
      // ILicenseAndAppInformation lives in ObjectModel (the Factory resolves that interface) and the
      // manager inherits it; look it up there and register the instance against it too. Optional so
      // a build without it still works.
      var information = Reflect.TypeOrNull(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.ILicenseAndAppInformation");

      // The desktop licence manager is an obfuscated ets5n type; pick the richest implementation
      // that exposes the licence interface(s) and has a parameterless ctor.
      var candidates = Reflect.SafeTypes(A("ets5n"))
        .Where(t => !t.IsAbstract && !t.IsInterface
          && manager.IsAssignableFrom(t)
          && (information == null || information.IsAssignableFrom(t))
          && t.GetConstructor(Type.EmptyTypes) != null)
        .OrderByDescending(t => t.GetMembers(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic).Length)
        .ToList();
      if (candidates.Count == 0)
      {
        throw new FailureException("no ETS licence manager implementing ILicenseAndAppManager was found");
      }
      var instance = Reflect.New(Reflect.Ctor(candidates[0]));
      var contracts = information == null ? new[] { manager } : new[] { manager, information };
      _autofac.RegisterInstance(builder, instance, contracts);
    }

    private object CreateIsolatedRoot(object context)
    {
      Directory.CreateDirectory(_storeDirectory);
      var dataStore = _autofac.ResolveWith(context,
        Reflect.Type(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.IDataStore"), typeof(string), _storeDirectory);
      var factory = _autofac.Resolve(context, Reflect.Type(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.IFactory"));
      var usage = _autofac.Resolve(context, Reflect.Type(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.Usages.IUsageDataStore"));
      var uiDispatcher = Reflect.New(
        Reflect.Ctor(Reflect.Type(A("Knx.Ets.ViewModel"), "Knx.Ets.ViewModel.WpfDispatcherService"), "System.Windows.Threading.Dispatcher"),
        _dispatcher);

      var root = Reflect.New(
        Reflect.Ctor(Reflect.Type(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.Root"),
          "Knx.Ets.ObjectModel.IDataStore", "Knx.Ets.ObjectModel.IFactory",
          "Knx.Ets.ObjectModel.IDispatcher", "Knx.Ets.ObjectModel.Usages.IUsageDataStore"),
        dataStore, factory, uiDispatcher, usage);
      _isolatedRootCreated = true;
      return root;
    }

    private void StartupServices()
    {
      var viewModel = A("Knx.Ets.ViewModel");
      Reflect.New(Reflect.Ctor(Reflect.Type(viewModel, "Knx.Ets.ViewModel.ContainerService"), "Autofac.IComponentContext"), _container);

      try
      {
        var proxy = Reflect.Type(A("Knx.Ets.ObjectModelWindowsBase"), "Knx.Ets.ObjectModelWindowsBase.WeakEventListenerProxy");
        var dispatcher = _autofac.Resolve(_container, Reflect.Type(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.IDispatcher"));
        Reflect.SetStatic(proxy, "CurrentDispatcher", dispatcher);
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
      object dataStore;
      if (Reflect.TryGet(_root, "DataStore", out dataStore) && dataStore != null)
      {
        object pathValue;
        if (Reflect.TryGet(dataStore, "DataStorePath", out pathValue) && pathValue is string storePath
          && !PathEquals(storePath, _storeDirectory))
        {
          throw new FailureException("the ETS root uses the store '" + storePath + "' instead of the isolated one; stopping");
        }
      }

      // Touch master data so the export/validation path finds it loaded. // UNVERIFIED
      try { Reflect.Get(_root, "MasterData"); }
      catch (Exception) { }
    }

    private void InspectArchive()
    {
      _shape = ArchiveShape.Inspect(_inputFull);
      if (_shape.ProjectCount != 1)
      {
        throw new FailureException("the file contains " + _shape.ProjectCount + " project(s); exactly one is required", 2);
      }
      _inputProtected = _shape.IsProtected;
      _result.OutputProtected = false;
      if (_inputProtected && string.IsNullOrEmpty(_request.Password))
      {
        throw new FailureException("the project is password protected; the project password is needed", 5);
      }
      CountInputElements();
    }

    private void CountInputElements()
    {
      Stream stream = null;
      ZipArchive archive = null;
      try
      {
        if (!_inputProtected)
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
      var passwordProvider = BuildPasswordProvider();
      var store = Reflect.New(
        Reflect.Ctor(Reflect.Type(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.ZipContainerStore.ZipContainerDataStore"),
          "System.IO.FileInfo", "System.Func<System.String>"),
        new FileInfo(_inputFull), passwordProvider);
      if (store is IDisposable disposable)
      {
        _disposables.Add(disposable);
      }

      var projects = Reflect.Get(_root, "Projects") ?? throw new FailureException("Root.Projects is null");
      var import = projects.GetType()
        .GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
        .Where(m => m.Name == "Import")
        .FirstOrDefault(m => m.GetParameters().Length >= 1
          && Reflect.Format(m.GetParameters()[0].ParameterType) == "Knx.Ets.ObjectModel.IDataStore")
        ?? throw new FailureException("ProjectCollection.Import(IDataStore, ...) was not found");

      var installerGuid = InstallerGuid();
      var parameters = import.GetParameters();
      var arguments = new object[parameters.Length];
      for (var i = 0; i < parameters.Length; i++)
      {
        var parameter = parameters[i];
        var type = parameter.ParameterType;
        if (i == 0)
        {
          arguments[i] = store;
        }
        else if (parameter.Name == "installerGuid" && type == typeof(string))
        {
          arguments[i] = installerGuid;
        }
        else if (typeof(Delegate).IsAssignableFrom(type))
        {
          arguments[i] = ImportCallback(parameter);
        }
        else
        {
          arguments[i] = parameter.HasDefaultValue ? parameter.DefaultValue : null;
        }
      }

      var imported = new List<object>();
      foreach (var item in (IEnumerable)(OnDispatcherResult(() => Reflect.Call(import, projects, arguments))
        ?? throw new FailureException("ProjectCollection.Import returned null")))
      {
        imported.Add(item);
      }
      if (imported.Count != 1)
      {
        throw new FailureException("ETS imported " + imported.Count + " projects from the file; exactly one is required");
      }
      _project = imported[0];
    }

    private Delegate BuildPasswordProvider()
    {
      var type = typeof(Func<string>);
      if (string.IsNullOrEmpty(_request.Password))
      {
        return Reflect.ConstantDelegate(type, (string)null);
      }
      var password = _request.Password;
      Func<string> provider = () => password;
      return provider;
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
      // Accept the project store, install baggage; do not install external software.
      var result = invoke.ReturnType == typeof(bool)
        ? (object)(name != "installExternalSoftware")
        : null;
      return Reflect.ConstantDelegate(type, result);
    }

    private string InstallerGuid()
    {
      try
      {
        var service = Reflect.Type(A("Knx.Ets.ViewModel"), "Knx.Ets.ViewModel.ContainerService");
        var application = Reflect.Call(Reflect.Method(service, "GetApplication", true), null);
        return application == null ? null : Reflect.Get(application, "InstallerGuid") as string;
      }
      catch (Exception)
      {
        return null;
      }
    }

    private void ReadLicence()
    {
      var managerType = Reflect.Type(A("Knx.Ets.ViewModel"), "Knx.Ets.ViewModel.Interfaces.ILicenseAndAppManager");
      var manager = _autofac.Resolve(_container, managerType);
      try
      {
        Reflect.Call(Reflect.Method(managerType, "WaitForDongleManager", false, "System.Int32"), manager, 30000);
      }
      catch (Exception) { }

      // CreateCertificate itself chooses the licence with the most devices; take the same one so
      // the stdout numbers match what the certificate call would use.
      object tuple = null;
      try
      {
        var exportVm = Reflect.Type(A("Knx.Ets.ViewModel"), "Knx.Ets.ViewModel.Application.Wizards.ExportWizard.ExportFileVM");
        tuple = Reflect.Call(Reflect.Method(exportVm, "GetLicenseWithMaxDevice", true), null);
      }
      catch (Exception) { }

      object licence = null;
      if (tuple != null)
      {
        object item1, item2;
        if (Reflect.TryGet(tuple, "Item1", out item1))
        {
          licence = item1;
        }
        if (Reflect.TryGet(tuple, "Item2", out item2) && item2 != null)
        {
          _result.LicensedDevices = Convert.ToInt32(item2, CultureInfo.InvariantCulture);
        }
      }

      _licence = licence;
      if (licence != null)
      {
        _result.LicenseName = ReadFirst(licence, "PackageName", "Label", "Name", "CustomerName", "RegisteredTo", "OwnerName");
        _result.LicenseNumber = ReadFirst(licence, "LicenseNumber", "Number", "RegistrationNumber", "SerialNumber", "DongleID", "DongleId");
      }
      else
      {
        _result.LicensedDevices = 0;
        _result.Notes.Add("no ETS dongle or licence was found; the export will be written without a certificate.");
      }
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
      var project = _project ?? throw new FailureException("no project was imported");
      var outputPath = Path.Combine(_workDirectory, "out", "export.knxproj");
      Directory.CreateDirectory(Path.GetDirectoryName(outputPath));
      if (File.Exists(outputPath))
      {
        throw new FailureException("the work output file exists already");
      }

      var extraDir = Path.Combine(_workDirectory, "extra");
      Directory.CreateDirectory(extraDir);

      var exporterType = FindExporterType();
      var zipArchiveType = Reflect.Type(A("SharpCompress"), "SharpCompress.Archives.Zip.ZipArchive");
      var compressionType = Reflect.Type(A("SharpCompress"), "SharpCompress.Common.CompressionType");
      var projectStoreType = Reflect.Type(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.ZipContainerStore.ZipContainerProjectStore");
      var projectType = Reflect.Type(A("Knx.Ets.ObjectModel"), "Knx.Ets.ObjectModel.Project.Project");

      var exporter = _autofac.Resolve(_container, exporterType);
      var export = exporterType.GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
        .Where(m => m.Name == "Export")
        .FirstOrDefault(m => m.GetParameters().Length >= 5
          && Reflect.Format(m.GetParameters()[0].ParameterType) == "Knx.Ets.ObjectModel.IProjectStore"
          && Reflect.Format(m.GetParameters()[1].ParameterType) == "Knx.Ets.ObjectModel.IProjectStore")
        ?? throw new FailureException("Exporter.Export(IProjectStore, IProjectStore, ...) was not found");

      var deviceCount = 0;
      OnDispatcher(() =>
      {
        OpenIfNeeded(project);
        deviceCount = Convert.ToInt32(Reflect.Get(project, "DeviceCount"), CultureInfo.InvariantCulture);
        Reflect.Call(Reflect.Method(projectType, "Save", false, "System.Boolean"), project, true);

        var zip = Reflect.Call(Reflect.Method(zipArchiveType, "Create", true), null)
          ?? throw new FailureException("ZipArchive.Create returned null");
        if (zip is IDisposable zipDisposable)
        {
          _disposables.Add(zipDisposable);
        }
        var projectId = Reflect.Get(project, "Id") ?? throw new FailureException("Project.Id is null");
        var zipStore = Reflect.New(Reflect.CtorFor(projectStoreType, projectId.GetType(), zipArchiveType), projectId, zip);
        var source = Reflect.Get(project, "Store") ?? throw new FailureException("Project.Store is null");

        // With an eligible licence, let ETS create and sign the certificate; without one, pass no
        // certify action so Exporter skips certification and the export succeeds for any device count.
        var licence = _licence;
        Action certify = null;
        if (licence != null)
        {
          var createCert = Reflect.Type(A("Knx.Ets.ViewModel"), "Knx.Ets.ViewModel.Application.Wizards.ExportWizard.ExportFileVM")
            .GetMethods(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic)
            .FirstOrDefault(m => m.Name == "CreateCertificate" && m.GetParameters().Length == 3
              && m.GetParameters()[0].ParameterType == projectType)
            ?? throw new FailureException("ExportFileVM.CreateCertificate(Project, ZipContainerProjectStore, license) was not found");
          certify = () => Reflect.Call(createCert, null, project, zipStore, licence);
        }

        var parameters = export.GetParameters();
        var arguments = new object[parameters.Length];
        arguments[0] = source;
        arguments[1] = zipStore;
        for (var i = 2; i < parameters.Length; i++)
        {
          var formatted = Reflect.Format(parameters[i].ParameterType);
          if (formatted == "System.Predicate<System.String>")
          {
            arguments[i] = (Predicate<string>)(_ => true);
          }
          else if (formatted == "System.IO.DirectoryInfo")
          {
            arguments[i] = new DirectoryInfo(extraDir);
          }
          else if (formatted == "System.Action")
          {
            arguments[i] = certify;
          }
          else
          {
            arguments[i] = parameters[i].HasDefaultValue ? parameters[i].DefaultValue : null;
          }
        }

        Reflect.Call(export, exporter, arguments);

        SaveZip(zip, compressionType, outputPath);
      });

      _result.Devices = deviceCount;
      if (_result.InputDevices >= 0 && _result.InputDevices != _result.Devices)
      {
        _result.Notes.Add("ETS imported " + _result.Devices + " of the " + _result.InputDevices
          + " devices of the file; the export may be incomplete.");
      }

      if (!File.Exists(outputPath))
      {
        throw new FailureException("ETS's export returned without writing the file");
      }
      _result.OutputSha256 = Digest.OfFile(outputPath);
      ReadCertificate(outputPath);
    }

    private void SaveZip(object zip, Type compressionType, string outputPath)
    {
      var deflate = Enum.Parse(compressionType, "Deflate");
      var fileInfo = new FileInfo(outputPath);

      var instance = zip.GetType()
        .GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
        .FirstOrDefault(m => m.Name == "SaveTo" && m.GetParameters().Length == 2
          && m.GetParameters()[0].ParameterType == typeof(FileInfo)
          && m.GetParameters()[1].ParameterType == compressionType);
      if (instance != null)
      {
        Reflect.Call(instance, zip, fileInfo, deflate);
        return;
      }

      // SaveTo(FileInfo, CompressionType) is a writable-archive extension in this build.
      foreach (var type in Reflect.SafeTypes(A("SharpCompress")))
      {
        var extension = type.GetMethods(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic)
          .FirstOrDefault(m => m.Name == "SaveTo" && m.GetParameters().Length == 3
            && m.GetParameters()[0].ParameterType.IsInstanceOfType(zip)
            && m.GetParameters()[1].ParameterType == typeof(FileInfo)
            && m.GetParameters()[2].ParameterType == compressionType);
        if (extension != null)
        {
          Reflect.Call(extension, null, zip, fileInfo, deflate);
          return;
        }
      }
      throw new FailureException("SharpCompress SaveTo(FileInfo, CompressionType) was not found");
    }

    private Type FindExporterType()
    {
      // Export/Exporter names are stable, but the namespace shifts between builds; match the type
      // the import/export registration uses: Exporter(IMasterDataStore, ...).
      var exporter = Reflect.SafeTypes(A("Knx.Ets.ObjectModel"))
        .Where(t => t.Name == "Exporter" && !t.IsAbstract)
        .FirstOrDefault(t => t.GetConstructors().Any(c => c.GetParameters().Length >= 1
          && c.GetParameters()[0].ParameterType.Name == "IMasterDataStore"));
      return exporter ?? throw new FailureException("the ETS Exporter type was not found");
    }

    private void OpenIfNeeded(object project)
    {
      object open;
      if (Reflect.TryGet(project, "IsOpen", out open) && open is bool isOpen && isOpen)
      {
        return;
      }
      Reflect.Call(Reflect.Method(project.GetType(), "Open", false), project);
    }

    private void ReadCertificate(string file)
    {
      using (var archive = ZipFile.OpenRead(file))
      {
        var hasCertificate = archive.Entries.Any(e => e.FullName.IndexOf('/') < 0
          && e.FullName.StartsWith("P-", StringComparison.OrdinalIgnoreCase)
          && e.FullName.EndsWith(".certificate", StringComparison.OrdinalIgnoreCase));
        _result.Certificate = hasCertificate;
      }
    }

    private void VerifyOutput()
    {
      if (!_result.Certificate)
      {
        return;
      }
      var file = Path.Combine(_workDirectory, "out", "export.knxproj");
      var directory = Path.Combine(_workDirectory, "check-output");
      Directory.CreateDirectory(directory);
      try
      {
        ZipFile.ExtractToDirectory(file, directory);
      }
      catch (Exception)
      {
        _result.Notes.Add("the export could not be unpacked for the signature check.");
        return;
      }

      MethodInfo check;
      try
      {
        check = Reflect.Method(Reflect.Type(LoadSigningAssembly(), "Knx.Ets.XmlSigning.XmlSigning"),
          "CheckDirectorySignature", true, "System.String", "System.String[]");
      }
      catch (Exception)
      {
        _result.Notes.Add("the ETS signature checker was not reachable; the certificate was not validated.");
        return;
      }

      var valid = true;
      var any = false;
      foreach (var sub in Directory.GetDirectories(directory)
        .Where(d => Path.GetFileName(d).StartsWith("P-", StringComparison.OrdinalIgnoreCase))
        .OrderBy(d => d, StringComparer.Ordinal))
      {
        any = true;
        var state = Reflect.Call(check, null, sub, null);
        if ((state?.ToString() ?? "null") != "SignatureFoundAndValid")
        {
          valid = false;
        }
      }
      _result.Validation = any && valid;
      if (!_result.Validation)
      {
        _result.Notes.Add("ETS wrote a certificate, but its signature check did not report the export as valid.");
      }
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

    private void Cleanup()
    {
      try
      {
        if (_project != null && Reflect.TryGet(_project, "IsOpen", out var open) && open is bool isOpen && isOpen)
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
    }

    private void OnDispatcher(Action change)
    {
      OnDispatcherResult(() => { change(); return null; });
    }

    private object OnDispatcherResult(Func<object> change)
    {
      Exception thrown = null;
      object value = null;
      var completed = false;
      try
      {
        _dispatcher.Invoke((Action)(() =>
        {
          try
          {
            value = change();
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
        }), DispatcherPriority.Send, CancellationToken.None, TimeSpan.FromMinutes(20));
      }
      catch (TimeoutException)
      {
        throw new FailureException("ETS did not finish within 20 minutes on its dispatcher thread");
      }
      if (thrown != null)
      {
        throw new EtsInvocationException(thrown);
      }
      if (!completed)
      {
        throw new FailureException("ETS's dispatcher did not run the call in time");
      }
      return value;
    }

    private bool IsInEtsDirectory(string location)
    {
      return !string.IsNullOrEmpty(location) && IsInside(location, _etsDirectory, true);
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

    public static Type TypeOrNull(Assembly assembly, string fullName)
    {
      return assembly.GetType(fullName, false);
    }

    public static Type[] SafeTypes(Assembly assembly)
    {
      try
      {
        return assembly.GetTypes();
      }
      catch (ReflectionTypeLoadException ex)
      {
        return ex.Types.Where(t => t != null).ToArray();
      }
    }

    public static ConstructorInfo Ctor(Type type, params string[] parameterTypes)
    {
      return type.GetConstructors(AnyInstance).FirstOrDefault(c => Matches(c.GetParameters(), parameterTypes))
        ?? throw new FailureException("constructor " + type.FullName + "(" + string.Join(", ", parameterTypes) + ") was not found");
    }

    public static ConstructorInfo CtorFor(Type type, params Type[] parameterTypes)
    {
      return type.GetConstructors(AnyInstance).FirstOrDefault(c =>
      {
        var parameters = c.GetParameters();
        if (parameters.Length != parameterTypes.Length)
        {
          return false;
        }
        for (var i = 0; i < parameters.Length; i++)
        {
          if (!parameters[i].ParameterType.IsAssignableFrom(parameterTypes[i]))
          {
            return false;
          }
        }
        return true;
      }) ?? throw new FailureException("no constructor of " + type.FullName + " accepts the given argument types");
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
    private readonly MethodInfo _registerInstance;
    private readonly MethodInfo _registerModuleInstance;
    private readonly MethodInfo _registerModuleStatic;
    private readonly MethodInfo _build;

    public ContainerBridge()
    {
      _componentContext = Reflect.Type("Autofac.IComponentContext");
      _parameter = Reflect.Type("Autofac.Core.Parameter");
      _typedParameter = Reflect.Ctor(Reflect.Type("Autofac.TypedParameter"), "System.Type", "System.Object");
      _resolve = Reflect.Method(Reflect.Type("Autofac.ResolutionExtensions"), "Resolve", true,
        "Autofac.IComponentContext", "System.Type", "Autofac.Core.Parameter[]");
      _containerBuilder = Reflect.Type("Autofac.ContainerBuilder");

      var registration = Reflect.Type("Autofac.RegistrationExtensions");
      _register = registration
        .GetMethods(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic)
        .FirstOrDefault(m => m.Name == "Register" && m.IsGenericMethodDefinition
          && m.GetGenericArguments().Length == 1 && m.GetParameters().Length == 2
          && m.GetParameters()[1].ParameterType.IsGenericType
          && m.GetParameters()[1].ParameterType.GetGenericTypeDefinition() == typeof(Func<,>))
        ?? throw new FailureException("Autofac.RegistrationExtensions.Register<T>(ContainerBuilder, Func<IComponentContext, T>) was not found");
      _registerInstance = registration
        .GetMethods(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic)
        .FirstOrDefault(m => m.Name == "RegisterInstance" && m.IsGenericMethodDefinition
          && m.GetGenericArguments().Length == 1 && m.GetParameters().Length == 2
          && m.GetParameters()[0].ParameterType == _containerBuilder)
        ?? throw new FailureException("Autofac.RegistrationExtensions.RegisterInstance<T>(ContainerBuilder, T) was not found");

      // RegisterModule is an instance method in newer Autofac, a static extension in older builds.
      _registerModuleInstance = _containerBuilder.GetMethods(BindingFlags.Instance | BindingFlags.Public)
        .FirstOrDefault(m => m.Name == "RegisterModule" && !m.IsGenericMethodDefinition
          && m.GetParameters().Length == 1 && m.GetParameters()[0].ParameterType.Name == "IModule");
      _registerModuleStatic = _registerModuleInstance != null ? null : FindStatic("RegisterModule", 2, _containerBuilder);
      if (_registerModuleInstance == null && _registerModuleStatic == null)
      {
        throw new FailureException("Autofac RegisterModule(ContainerBuilder, IModule) was not found");
      }
      _build = _containerBuilder.GetMethods(BindingFlags.Instance | BindingFlags.Public)
        .FirstOrDefault(m => m.Name == "Build")
        ?? throw new FailureException("ContainerBuilder.Build was not found");
    }

    public object NewBuilder()
    {
      return Reflect.New(Reflect.Ctor(_containerBuilder));
    }

    public object Build(object builder)
    {
      var parameters = _build.GetParameters();
      var arguments = parameters.Select(p => p.HasDefaultValue ? p.DefaultValue : Activator.CreateInstance(p.ParameterType)).ToArray();
      return Reflect.Call(_build, builder, arguments);
    }

    public void RegisterModule(object builder, object module)
    {
      if (_registerModuleInstance != null)
      {
        Reflect.Call(_registerModuleInstance, builder, module);
      }
      else
      {
        Reflect.Call(_registerModuleStatic, null, builder, module);
      }
    }

    public void RegisterInstance(object builder, object instance, Type[] services)
    {
      var registration = _registerInstance.MakeGenericMethod(instance.GetType()).Invoke(null, new[] { builder, instance })
        ?? throw new FailureException("Autofac RegisterInstance returned null");
      registration = FindBuilderMethod(registration, "As", typeof(Type[])).Invoke(registration, new object[] { services });
      FindBuilderMethod(registration, "SingleInstance").Invoke(registration, null);
    }

    public void RegisterSingleInstance(object builder, Type implementation, Type[] services, Func<object, object> factory)
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

    private static MethodInfo FindStatic(string name, int parameterCount, Type firstParameter)
    {
      foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
      {
        var assemblyName = assembly.GetName().Name ?? "";
        if (!assemblyName.StartsWith("Autofac", StringComparison.Ordinal))
        {
          continue;
        }
        Type[] types;
        try { types = assembly.GetTypes(); }
        catch (ReflectionTypeLoadException ex) { types = ex.Types.Where(t => t != null).ToArray(); }
        foreach (var type in types)
        {
          var method = type.GetMethods(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic)
            .FirstOrDefault(m => m.Name == name && !m.IsGenericMethodDefinition
              && m.GetParameters().Length == parameterCount
              && m.GetParameters()[0].ParameterType == firstParameter);
          if (method != null)
          {
            return method;
          }
        }
      }
      return null;
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
        Path.Combine(programData, "KNX", "ETS5", "ProductStore"),
        Path.Combine(programData, "KNX", "ETS5", "ProjectStore"),
      };
      if (localAppData.Length > 0)
      {
        locations.Add(Path.Combine(localAppData, "KNX", "ETS5", "Cache"));
        locations.Add(Path.Combine(localAppData, "KNX", "ETS5", "My Products"));
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
